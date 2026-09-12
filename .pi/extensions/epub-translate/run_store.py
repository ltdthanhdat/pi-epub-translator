#!/usr/bin/env python3
"""Durable state and EPUB mechanics for the project-local Pi extension."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
import zipfile
from contextlib import contextmanager
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
import xml.etree.ElementTree as ET


LEASE_SECONDS = 120


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    xhtml_path: str
    locator: str
    input_path: str
    lease_token: str
    attempts: int
    backend: str
    model: str
    thinking: str
    target_language: str
    target_language_code: str
    glossary_path: str

    @classmethod
    def from_row(cls, row: sqlite3.Row, config: dict[str, object]) -> "ClaimedJob":
        return cls(
            id=row["id"],
            xhtml_path=row["xhtml_path"],
            locator=row["locator"],
            input_path=row["input_path"],
            lease_token=row["lease_token"],
            attempts=row["attempts"],
            backend=str(config.get("backend", "")),
            model=str(config.get("model", "")),
            thinking=str(config.get("thinking", "")),
            target_language=str(config.get("target_language", "")),
            target_language_code=str(config.get("target_language_code", "")),
            glossary_path=str(config.get("glossary_path", "")),
        )

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class RunStore:
    """Small SQLite-backed state machine shared by extension and workers."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    config_json TEXT NOT NULL DEFAULT '{}',
                    state TEXT NOT NULL DEFAULT 'running',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    merge_state TEXT NOT NULL DEFAULT 'pending',
                    merge_worker_id TEXT,
                    started_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    output_path TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY,
                    xhtml_path TEXT NOT NULL,
                    locator TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    result_path TEXT,
                    state TEXT NOT NULL DEFAULT 'pending',
                    worker_id TEXT,
                    lease_token TEXT,
                    lease_until INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    UNIQUE(xhtml_path, locator)
                );
                CREATE INDEX IF NOT EXISTS jobs_state_lease_idx
                    ON jobs(state, lease_until);
                """
            )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 30000")
        db.execute("PRAGMA journal_mode = WAL")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _config(self) -> dict[str, object]:
        with self.connection() as db:
            row = db.execute("SELECT config_json FROM runs WHERE id=1").fetchone()
            if row is None:
                raise ValueError("run has not been created")
            return json.loads(row["config_json"])

    def create_run(self, config: dict[str, object], now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        normalized = dict(config)
        normalized.setdefault("max_attempts", 3)
        with self.connection() as db:
            db.execute(
                """INSERT INTO runs
                   (id, config_json, state, cancel_requested, merge_state, started_at, updated_at)
                   VALUES (1, ?, 'running', 0, 'pending', ?, ?)""",
                (json.dumps(normalized, ensure_ascii=False, sort_keys=True), now, now),
            )

    def update_run(self, **values: object) -> None:
        allowed = {
            "state",
            "merge_state",
            "merge_worker_id",
            "output_path",
            "error",
            "cancel_requested",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unknown run fields: {', '.join(sorted(unknown))}")
        if not values:
            return
        values["updated_at"] = int(time.time())
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.connection() as db:
            db.execute(
                f"UPDATE runs SET {assignments} WHERE id=1",
                tuple(values.values()),
            )

    def add_jobs(self, jobs: list[dict[str, str]]) -> None:
        with self.connection() as db:
            db.executemany(
                """INSERT INTO jobs (xhtml_path, locator, input_path)
                   VALUES (?, ?, ?)""",
                [(job["xhtml_path"], job["locator"], job["input_path"]) for job in jobs],
            )

    def config(self) -> dict[str, object]:
        return self._config()

    def claim(self, worker_id: str, now: int | None = None) -> ClaimedJob | None:
        now = int(time.time()) if now is None else now
        token = uuid.uuid4().hex
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute(
                "SELECT cancel_requested, config_json FROM runs WHERE id=1"
            ).fetchone()
            if run is None:
                raise ValueError("run has not been created")
            if run["cancel_requested"]:
                return None
            config = json.loads(run["config_json"])
            max_attempts = int(config.get("max_attempts", 3))
            row = db.execute(
                """SELECT * FROM jobs
                   WHERE (state='pending'
                      OR (state='leased' AND lease_until < ?)
                      OR (state='failed' AND attempts < ?))
                   ORDER BY id LIMIT 1""",
                (now, max_attempts),
            ).fetchone()
            if row is None:
                return None
            changed = db.execute(
                """UPDATE jobs
                   SET state='leased', worker_id=?, lease_token=?, lease_until=?,
                       attempts=attempts+1, error=NULL
                   WHERE id=? AND
                         (state='pending'
                          OR (state='leased' AND lease_until < ?)
                          OR (state='failed' AND attempts < ?))""",
                (worker_id, token, now + LEASE_SECONDS, row["id"], now, max_attempts),
            ).rowcount
            if changed != 1:
                return None
            claimed = db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
            return ClaimedJob.from_row(claimed, config)

    def heartbeat(
        self,
        job_id: int,
        worker_id: str,
        lease_token: str,
        now: int | None = None,
    ) -> bool:
        now = int(time.time()) if now is None else now
        with self.connection() as db:
            return (
                db.execute(
                    """UPDATE jobs SET lease_until=?
                       WHERE id=? AND state='leased' AND worker_id=?
                         AND lease_token=? AND lease_until >= ?""",
                    (now + LEASE_SECONDS, job_id, worker_id, lease_token, now),
                ).rowcount
                == 1
            )

    def complete(
        self,
        job_id: int,
        worker_id: str,
        lease_token: str,
        result_path: str,
    ) -> bool:
        with self.connection() as db:
            changed = db.execute(
                """UPDATE jobs SET state='done', result_path=?, worker_id=NULL,
                       lease_token=NULL, lease_until=NULL, error=NULL
                   WHERE id=? AND state='leased' AND worker_id=? AND lease_token=?""",
                (result_path, job_id, worker_id, lease_token),
            ).rowcount
            if changed:
                db.execute("UPDATE runs SET updated_at=? WHERE id=1", (int(time.time()),))
            return changed == 1

    def fail(
        self,
        job_id: int,
        worker_id: str,
        lease_token: str,
        error: str,
    ) -> bool:
        with self.connection() as db:
            changed = db.execute(
                """UPDATE jobs SET state='failed', error=?, worker_id=NULL,
                       lease_token=NULL, lease_until=NULL
                   WHERE id=? AND state='leased' AND worker_id=? AND lease_token=?""",
                (error[:4000], job_id, worker_id, lease_token),
            ).rowcount
            if changed:
                db.execute("UPDATE runs SET updated_at=? WHERE id=1", (int(time.time()),))
            return changed == 1

    def request_cancel(self) -> bool:
        with self.connection() as db:
            return db.execute(
                "UPDATE runs SET cancel_requested=1, updated_at=? WHERE id=1",
                (int(time.time()),),
            ).rowcount == 1

    def clear_cancel(self) -> bool:
        with self.connection() as db:
            return db.execute(
                "UPDATE runs SET cancel_requested=0, state='running', error=NULL, updated_at=? WHERE id=1",
                (int(time.time()),),
            ).rowcount == 1

    def retry_failed(self, job_ids: list[int] | None = None) -> int:
        with self.connection() as db:
            if job_ids:
                marks = ",".join("?" for _ in job_ids)
                cursor = db.execute(
                    f"""UPDATE jobs SET state='pending', attempts=0, error=NULL
                        WHERE state='failed' AND id IN ({marks})""",
                    tuple(job_ids),
                )
            else:
                cursor = db.execute(
                    "UPDATE jobs SET state='pending', attempts=0, error=NULL WHERE state='failed'"
                )
            db.execute(
                """UPDATE runs SET cancel_requested=0, state='running', error=NULL, updated_at=?
                   WHERE id=1""",
                (int(time.time()),),
            )
            return cursor.rowcount

    def try_claim_merge(self, worker_id: str) -> bool:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute(
                "SELECT cancel_requested, merge_state FROM runs WHERE id=1"
            ).fetchone()
            if run is None or run["cancel_requested"] or run["merge_state"] != "pending":
                return False
            unfinished = db.execute(
                "SELECT 1 FROM jobs WHERE state != 'done' LIMIT 1"
            ).fetchone()
            if unfinished is not None:
                return False
            return (
                db.execute(
                    """UPDATE runs SET merge_state='leased', merge_worker_id=?, updated_at=?
                       WHERE id=1 AND merge_state='pending'""",
                    (worker_id, int(time.time())),
                ).rowcount
                == 1
            )

    def job(self, job_id: int) -> sqlite3.Row:
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError(f"job not found: {job_id}")
        return row

    def summary(self) -> dict[str, object]:
        with self.connection() as db:
            run = db.execute("SELECT * FROM runs WHERE id=1").fetchone()
            if run is None:
                raise ValueError("run has not been created")
            counts = {
                row["state"]: row["count"]
                for row in db.execute("SELECT state, COUNT(*) AS count FROM jobs GROUP BY state")
            }
            total = sum(counts.values())
            return {
                "state": run["state"],
                "mergeState": run["merge_state"],
                "total": total,
                "done": counts.get("done", 0),
                "leased": counts.get("leased", 0),
                "failed": counts.get("failed", 0),
                "pending": counts.get("pending", 0),
                "startedAt": run["started_at"],
                "cancelRequested": bool(run["cancel_requested"]),
                "outputPath": run["output_path"],
                "error": run["error"],
            }


OPF_NS = "http://www.idpf.org/2007/opf"
EPUB_NS = "http://www.idpf.org/2007/ops"
XML_NS = "http://www.w3.org/XML/1998/namespace"
EPUB_TYPE = f"{{{EPUB_NS}}}type"
TEXT_BLOCKS = {"p", "li", "td", "th", "caption", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6"}


@dataclass(frozen=True)
class DocumentInfo:
    path: str
    in_spine: bool
    is_nav: bool
    properties: tuple[str, ...]
    epub_types: tuple[str, ...]
    title: str
    sample: str

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "in_spine": self.in_spine,
            "is_nav": self.is_nav,
            "properties": list(self.properties),
            "epub_types": list(self.epub_types),
            "title": self.title,
            "sample": self.sample,
        }


@dataclass(frozen=True)
class Candidate:
    term: str
    frequency: int
    document_count: int
    contexts: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "term": self.term,
            "frequency": self.frequency,
            "document_count": self.document_count,
            "contexts": list(self.contexts),
        }


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def find_opf(workspace: Path) -> Path:
    container = workspace / "META-INF" / "container.xml"
    if container.is_file():
        root = ET.parse(container).getroot()
        for element in root.iter():
            if local_name(element.tag) == "rootfile" and element.get("full-path"):
                return workspace / element.get("full-path")
    fallback = workspace / "OEBPS" / "content.opf"
    if fallback.is_file():
        return fallback
    matches = sorted(workspace.rglob("*.opf"))
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"could not find EPUB package document under {workspace}")


def manifest_documents(workspace: Path):
    opf_path = find_opf(workspace)
    root = ET.parse(opf_path).getroot()
    manifest: dict[str, tuple[Path, str, tuple[str, ...]]] = {}
    for item in root.iter():
        if local_name(item.tag) != "item" or item.get("media-type") != "application/xhtml+xml":
            continue
        href = (item.get("href") or "").split("#", 1)[0]
        path = (opf_path.parent / Path(href)).resolve()
        try:
            relative = path.relative_to(workspace.resolve()).as_posix()
        except ValueError as error:
            raise ValueError(f"manifest item escapes EPUB root: {href}") from error
        properties = tuple((item.get("properties") or "").split())
        manifest[item.get("id", "")] = (Path(relative), path.as_posix(), properties)
    spine_ids: list[str] = []
    for itemref in root.iter():
        if local_name(itemref.tag) == "itemref" and itemref.get("idref"):
            spine_ids.append(itemref.get("idref"))
    return opf_path, manifest, spine_ids


def text_content(root: ET.Element, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", " ".join(root.itertext())).strip()[:limit]


def inspect_documents(workspace: Path) -> list[DocumentInfo]:
    workspace = Path(workspace)
    _opf_path, manifest, spine_ids = manifest_documents(workspace)
    spine_paths = {manifest[item_id][0].as_posix() for item_id in spine_ids if item_id in manifest}
    documents: list[DocumentInfo] = []
    for _item_id, (relative, absolute, properties) in manifest.items():
        path = Path(absolute)
        root = ET.parse(path).getroot()
        epub_types = sorted({
            value
            for element in root.iter()
            for attribute, value in element.attrib.items()
            if local_name(attribute) == "type"
            for value in value.split()
        })
        title = ""
        for element in root.iter():
            if local_name(element.tag) in {"title", "h1", "h2"}:
                title = text_content(element, 240)
                if title:
                    break
        documents.append(
            DocumentInfo(
                path=relative.as_posix(),
                in_spine=relative.as_posix() in spine_paths,
                is_nav="nav" in properties,
                properties=properties,
                epub_types=tuple(epub_types),
                title=title,
                sample=text_content(root),
            )
        )
    return documents


def iter_blocks(element: ET.Element, locator: str = ""):
    if local_name(element.tag) in TEXT_BLOCKS:
        yield locator
        return
    for index, child in enumerate(element):
        child_locator = f"{locator}/{index}" if locator else str(index)
        yield from iter_blocks(child, child_locator)


def element_at(root: ET.Element, locator: str) -> ET.Element:
    element = root
    for index in locator.split("/"):
        element = list(element)[int(index)]
    return element


def protect_empty_pagebreaks(source: str, job_id: int):
    root = ET.fromstring(source)
    markers: dict[str, str] = {}
    number = 0
    for parent in root.iter():
        index = 0
        while index < len(parent):
            child = parent[index]
            if (
                local_name(child.tag) == "span"
                and child.attrib.get(EPUB_TYPE) == "pagebreak"
                and not (child.text or "").strip()
                and not len(child)
            ):
                marker = f"[[KEEP_PAGEBREAK_{job_id}_{number}]]"
                number += 1
                tail = child.tail or ""
                child.tail = None
                markers[marker] = ET.tostring(child, encoding="unicode")
                if index:
                    parent[index - 1].tail = (parent[index - 1].tail or "") + marker + tail
                else:
                    parent.text = (parent.text or "") + marker + tail
                parent.remove(child)
                continue
            index += 1
    return ET.tostring(root, encoding="unicode"), markers


def restore_pagebreaks(translated: str, markers: dict[str, str]) -> str:
    for marker, markup in markers.items():
        if translated.count(marker) != 1:
            raise ValueError("pagebreak marker changed")
        translated = translated.replace(marker, markup)
    return translated


def validate_fragment(source: str, translated: str) -> None:
    if not translated.strip():
        raise ValueError("empty result")
    before, after = ET.fromstring(source), ET.fromstring(translated)

    def compare(left: ET.Element, right: ET.Element) -> None:
        if left.tag != right.tag or left.attrib != right.attrib or len(left) != len(right):
            raise ValueError("structure changed")
        for left_child, right_child in zip(left, right):
            compare(left_child, right_child)

    compare(before, after)


def _candidate_tokens(text: str) -> list[str]:
    cleaned = re.sub(r"https?://\S+|www\.\S+", " ", text, flags=re.IGNORECASE)
    return re.findall(r"[^\W_]+(?:['’\-][^\W_]+)*", cleaned, flags=re.UNICODE)


STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "in", "is", "it",
    "of", "on", "or", "the", "to", "was", "were", "with", "this", "that", "these",
    "those", "et", "en", "un", "une",
}


def _looks_like_candidate(tokens: list[str]) -> bool:
    if len(tokens) < 2 or len(tokens) > 4:
        return False
    if any(token.casefold() in STOPWORDS and len(token) > 1 for token in tokens):
        return False
    if any(token.isdigit() for token in tokens):
        return False
    return all(token[0].isupper() for token in tokens if token)


def glossary_candidates(samples: list[str], limit: int = 200) -> list[Candidate]:
    occurrences: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for document_index, sample in enumerate(samples):
        tokens = _candidate_tokens(sample)
        seen_in_document: set[str] = set()
        for start in range(len(tokens)):
            for size in range(2, 5):
                phrase_tokens = tokens[start : start + size]
                if len(phrase_tokens) != size or not _looks_like_candidate(phrase_tokens):
                    continue
                phrase = " ".join(phrase_tokens)
                key = phrase.casefold()
                if key in seen_in_document:
                    continue
                seen_in_document.add(key)
                occurrences[key].append((document_index, phrase))
    ranked: list[Candidate] = []
    for key, matches in occurrences.items():
        contexts = tuple(match[1] for match in matches[:3])
        ranked.append(
            Candidate(
                term=matches[0][1],
                frequency=len(matches),
                document_count=len({index for index, _phrase in matches}),
                contexts=contexts,
            )
        )
    ranked.sort(
        key=lambda candidate: (
            -candidate.frequency,
            -candidate.document_count,
            -len(candidate.term.split()),
            candidate.term.casefold(),
        )
    )
    return ranked[:limit]


def _replace_at(root: ET.Element, locator: str, replacement: ET.Element) -> None:
    parts = locator.split("/")
    parent = root if len(parts) == 1 else element_at(root, "/".join(parts[:-1]))
    index = int(parts[-1])
    children = list(parent)
    parent.remove(children[index])
    parent.insert(index, replacement)


def _build_epub(staging: Path, output: Path) -> None:
    mimetype = staging / "mimetype"
    if not mimetype.is_file():
        raise ValueError("missing EPUB mimetype")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w") as archive:
        archive.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(staging.rglob("*")):
            if path.is_dir() or path == mimetype:
                continue
            archive.write(path, path.relative_to(staging).as_posix(), compress_type=zipfile.ZIP_DEFLATED)
    temporary.replace(output)


def verify_output(staging: Path, output: Path) -> None:
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("invalid EPUB archive")
        infos = archive.infolist()
        if not infos or infos[0].filename != "mimetype" or infos[0].compress_type != zipfile.ZIP_STORED:
            raise ValueError("invalid EPUB mimetype entry")
        expected = {
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file()
        }
        if set(archive.namelist()) != expected:
            raise ValueError("EPUB members changed unexpectedly")
    epubcheck = shutil.which("epubcheck")
    if epubcheck:
        completed = subprocess.run([epubcheck, str(output)], capture_output=True, text=True)
        if completed.returncode:
            raise ValueError(completed.stderr.strip() or completed.stdout.strip() or "epubcheck failed")


def merge_run(run_dir: Path) -> Path:
    run_dir = Path(run_dir)
    store = RunStore(run_dir / "run.sqlite")
    summary = store.summary()
    if summary["cancelRequested"]:
        raise ValueError("run was cancelled")
    if summary["total"] == 0 or summary["done"] != summary["total"]:
        raise ValueError("unfinished jobs")
    config = store.config()
    source = run_dir / "source"
    staging = run_dir / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(source, staging)
    jobs_by_path: dict[str, list[sqlite3.Row]] = defaultdict(list)
    with store.connection() as db:
        jobs = db.execute("SELECT * FROM jobs WHERE state='done' ORDER BY id").fetchall()
    for job in jobs:
        jobs_by_path[job["xhtml_path"]].append(job)
    for relative_path, path_jobs in jobs_by_path.items():
        xhtml_path = staging / relative_path
        root = ET.parse(xhtml_path).getroot()
        for job in sorted(path_jobs, key=lambda item: [int(part) for part in item["locator"].split("/")], reverse=True):
            source_element = ET.tostring(element_at(root, job["locator"]), encoding="unicode")
            translated = Path(job["result_path"]).read_text()
            validate_fragment(source_element, translated)
            _replace_at(root, job["locator"], ET.fromstring(translated))
        language = str(config.get("target_language_code", ""))
        if language:
            root.set("lang", language)
            root.set(f"{{{XML_NS}}}lang", language)
        ET.ElementTree(root).write(xhtml_path, encoding="utf-8", xml_declaration=True)
    output = Path(str(config.get("output_path", run_dir / "output.epub")))
    _build_epub(staging, output)
    verify_output(staging, output)
    store.update_run(state="completed", merge_state="completed", output_path=str(output), error=None)
    return output


def _safe_extract(epub_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(epub_path) as archive:
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"unsafe EPUB member: {info.filename}")
            archive.extract(info, destination)


def _run_path(run_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else run_dir / path


def sample_documents(workspace: Path, selected_paths: list[str], sample_count: int = 6) -> list[dict[str, object]]:
    selected = set(selected_paths)
    documents = [document for document in inspect_documents(workspace) if document.path in selected]
    if len(documents) <= sample_count:
        chosen = documents
    else:
        indexes = {round(index * (len(documents) - 1) / (sample_count - 1)) for index in range(sample_count)}
        chosen = [document for index, document in enumerate(documents) if index in indexes]
    return [document.as_dict() for document in chosen]


def prepare_run(
    source_epub: Path,
    run_dir: Path,
    config: dict[str, object],
    selected_paths: list[str],
) -> dict[str, object]:
    source_epub = Path(source_epub)
    run_dir = Path(run_dir)
    if not source_epub.is_file():
        raise ValueError(f"source EPUB not found: {source_epub}")
    if run_dir.exists() and any(run_dir.iterdir()):
        raise ValueError(f"run directory is not empty: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    source = run_dir / "source"
    _safe_extract(source_epub, source)
    available = {document.path for document in inspect_documents(source)}
    normalized_paths = list(dict.fromkeys(selected_paths))
    unknown = set(normalized_paths) - available
    if unknown:
        raise ValueError(f"selected document not found: {sorted(unknown)[0]}")
    if not normalized_paths:
        raise ValueError("at least one XHTML document must be selected")
    glossary_text = str(config.get("glossary_text", ""))
    glossary_path = run_dir / "glossary.txt"
    glossary_path.write_text(glossary_text, encoding="utf-8")
    persisted = dict(config)
    persisted.pop("glossary_text", None)
    persisted["glossary_path"] = str(glossary_path)
    persisted["selected_paths"] = normalized_paths
    persisted.setdefault("max_attempts", 3)
    store = RunStore(run_dir / "run.sqlite")
    store.create_run(persisted)
    inputs = run_dir / "inputs"
    inputs.mkdir()
    jobs: list[dict[str, str]] = []
    for relative_path in normalized_paths:
        xhtml_path = source / relative_path
        root = ET.parse(xhtml_path).getroot()
        for locator in iter_blocks(root):
            element = element_at(root, locator)
            if not "".join(element.itertext()).strip():
                continue
            input_path = inputs / f"{len(jobs) + 1}.xhtml"
            input_path.write_text(ET.tostring(element, encoding="unicode"), encoding="utf-8")
            jobs.append({
                "xhtml_path": relative_path,
                "locator": locator,
                "input_path": str(input_path),
            })
    if not jobs:
        raise ValueError("no translatable XHTML blocks found")
    store.add_jobs(jobs)
    return store.summary()


def claim_payload(run_dir: Path, claimed: ClaimedJob) -> dict[str, object]:
    run_dir = Path(run_dir)
    source_path = _run_path(run_dir, claimed.input_path)
    raw = source_path.read_text(encoding="utf-8")
    protected, _markers = protect_empty_pagebreaks(raw, claimed.id)
    glossary = ""
    if claimed.glossary_path:
        glossary = _run_path(run_dir, claimed.glossary_path).read_text(encoding="utf-8")
    payload = claimed.as_dict()
    payload.update({"source": protected, "glossary": glossary})
    return payload


def complete_translation(
    run_dir: Path,
    job_id: int,
    worker_id: str,
    lease_token: str,
    translated: str,
) -> dict[str, object]:
    run_dir = Path(run_dir)
    store = RunStore(run_dir / "run.sqlite")
    job = store.job(job_id)
    raw = _run_path(run_dir, job["input_path"]).read_text(encoding="utf-8")
    _protected, markers = protect_empty_pagebreaks(raw, job_id)
    restored = restore_pagebreaks(translated.strip(), markers)
    validate_fragment(raw, restored)
    results = run_dir / "results"
    results.mkdir(parents=True, exist_ok=True)
    result_path = results / f"{job_id}-{lease_token}.xhtml"
    temporary = result_path.with_suffix(result_path.suffix + ".tmp")
    temporary.write_text(restored, encoding="utf-8")
    temporary.replace(result_path)
    accepted = store.complete(job_id, worker_id, lease_token, str(result_path))
    if not accepted:
        result_path.unlink(missing_ok=True)
    return {"accepted": accepted, "result_path": str(result_path), "job_id": job_id}


# The CLI is implemented after the pure helpers so extension tools can use one
# stable JSON boundary for both preparation and worker operations.
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("--workspace", required=True, type=Path)

    sample_parser = commands.add_parser("sample")
    sample_parser.add_argument("--workspace", required=True, type=Path)
    sample_parser.add_argument("--selected-json", required=True)
    sample_parser.add_argument("--count", type=int, default=6)

    candidate_parser = commands.add_parser("candidates")
    candidate_parser.add_argument("--samples-file", type=Path)
    candidate_parser.add_argument("--samples-json")

    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--source", required=True, type=Path)
    prepare_parser.add_argument("--run", required=True, type=Path)
    prepare_parser.add_argument("--config-json", required=True)
    prepare_parser.add_argument("--selected-json", required=True)

    for name in ("summary", "claim", "heartbeat", "complete", "fail", "cancel", "clear-cancel", "retry", "claim-merge", "merge"):
        command_parser = commands.add_parser(name)
        if name not in {"cancel", "clear-cancel"}:
            command_parser.add_argument("--run", required=True, type=Path)
        else:
            command_parser.add_argument("--run", required=True, type=Path)
        if name in {"claim", "claim-merge"}:
            command_parser.add_argument("--worker-id", required=True)
        if name == "heartbeat":
            command_parser.add_argument("--job-id", required=True, type=int)
            command_parser.add_argument("--worker-id", required=True)
            command_parser.add_argument("--lease-token", required=True)
        if name == "complete":
            command_parser.add_argument("--job-id", required=True, type=int)
            command_parser.add_argument("--worker-id", required=True)
            command_parser.add_argument("--lease-token", required=True)
            command_parser.add_argument("--translated-file", type=Path)
        if name == "fail":
            command_parser.add_argument("--job-id", required=True, type=int)
            command_parser.add_argument("--worker-id", required=True)
            command_parser.add_argument("--lease-token", required=True)
            command_parser.add_argument("--error", required=True)
        if name == "retry":
            command_parser.add_argument("--job-id", action="append", type=int)

    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = [document.as_dict() for document in inspect_documents(args.workspace)]
        elif args.command == "sample":
            result = sample_documents(args.workspace, json.loads(args.selected_json), args.count)
        elif args.command == "candidates":
            if bool(args.samples_file) == bool(args.samples_json):
                raise ValueError("provide exactly one of --samples-file or --samples-json")
            raw = args.samples_file.read_text(encoding="utf-8") if args.samples_file else args.samples_json
            result = [candidate.as_dict() for candidate in glossary_candidates(json.loads(raw))]
        elif args.command == "prepare":
            result = prepare_run(
                args.source,
                args.run,
                json.loads(args.config_json),
                json.loads(args.selected_json),
            )
        elif args.command == "summary":
            result = RunStore(args.run / "run.sqlite").summary()
        elif args.command == "claim":
            store = RunStore(args.run / "run.sqlite")
            claimed = store.claim(args.worker_id)
            result = {"job": claim_payload(args.run, claimed) if claimed else None}
        elif args.command == "heartbeat":
            result = {
                "ok": RunStore(args.run / "run.sqlite").heartbeat(
                    args.job_id, args.worker_id, args.lease_token
                )
            }
        elif args.command == "complete":
            translated = (
                args.translated_file.read_text(encoding="utf-8")
                if args.translated_file
                else os.sys.stdin.read()
            )
            result = complete_translation(
                args.run, args.job_id, args.worker_id, args.lease_token, translated
            )
        elif args.command == "fail":
            result = {
                "ok": RunStore(args.run / "run.sqlite").fail(
                    args.job_id, args.worker_id, args.lease_token, args.error
                )
            }
        elif args.command == "cancel":
            result = {"ok": RunStore(args.run / "run.sqlite").request_cancel()}
        elif args.command == "clear-cancel":
            result = {"ok": RunStore(args.run / "run.sqlite").clear_cancel()}
        elif args.command == "retry":
            result = {"reset": RunStore(args.run / "run.sqlite").retry_failed(args.job_id)}
        elif args.command == "claim-merge":
            result = {
                "claimed": RunStore(args.run / "run.sqlite").try_claim_merge(args.worker_id)
            }
        elif args.command == "merge":
            result = {"output": str(merge_run(args.run))}
        else:
            raise ValueError(f"unknown command: {args.command}")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
