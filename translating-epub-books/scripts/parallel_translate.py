#!/usr/bin/env python3
"""Run resumable parallel EPUB translation jobs."""

import argparse
import multiprocessing
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

from epub_tool import build, extract


LEASE_SECONDS = 900
OPF_NS = "{http://www.idpf.org/2007/opf}"
TEXT_BLOCKS = {"p", "li", "td", "th", "caption", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6"}
CODEX_MODELS = ("gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5")
CLAUDE_MODELS = ("opus", "sonnet", "haiku")


@dataclass(frozen=True)
class JobDraft:
    xhtml_path: str
    locator: str
    input_path: str
    backend: str
    model: str
    target_language: str


@dataclass(frozen=True)
class Settings:
    backend: str
    model: str
    target_language: str
    workers: int


@dataclass(frozen=True)
class Job(JobDraft):
    id: int
    attempts: int
    result_path: str | None

    @classmethod
    def from_row(cls, row):
        return cls(
            id=row["id"],
            xhtml_path=row["xhtml_path"],
            locator=row["locator"],
            input_path=row["input_path"],
            backend=row["backend"],
            model=row["model"],
            target_language=row["target_language"],
            attempts=row["attempts"],
            result_path=row["result_path"],
        )


class RunStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY,
                    xhtml_path TEXT NOT NULL,
                    locator TEXT NOT NULL UNIQUE,
                    input_path TEXT NOT NULL,
                    result_path TEXT,
                    backend TEXT NOT NULL,
                    model TEXT NOT NULL,
                    target_language TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    worker_id TEXT,
                    lease_until INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                );
                """
            )

    def connection(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def add_jobs(self, jobs):
        with self.connection() as db:
            db.executemany(
                """INSERT INTO jobs
                (xhtml_path, locator, input_path, backend, model, target_language)
                VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (j.xhtml_path, j.locator, j.input_path, j.backend, j.model, j.target_language)
                    for j in jobs
                ],
            )

    def set_settings(self, settings: Settings):
        with self.connection() as db:
            db.executemany(
                "INSERT OR REPLACE INTO runs(key, value) VALUES (?, ?)",
                [("backend", settings.backend), ("model", settings.model),
                 ("target_language", settings.target_language), ("workers", str(settings.workers))],
            )

    def settings(self):
        with self.connection() as db:
            values = dict(db.execute("SELECT key, value FROM runs"))
        return Settings(values["backend"], values["model"], values["target_language"], int(values["workers"]))

    def has_jobs(self):
        with self.connection() as db:
            return bool(db.execute("SELECT 1 FROM jobs LIMIT 1").fetchone())

    def done_jobs(self):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM jobs WHERE state='done' ORDER BY id").fetchall()
        return [Job.from_row(row) for row in rows]

    def failed_count(self):
        with self.connection() as db:
            return db.execute("SELECT COUNT(*) FROM jobs WHERE state='failed'").fetchone()[0]

    def lease(self, worker_id: str, now: int):
        with self.connection() as db:
            row = db.execute(
                """UPDATE jobs SET state='leased', worker_id=?, lease_until=?,
                   attempts=attempts+1
                   WHERE id=(SELECT id FROM jobs WHERE state='pending' OR
                     (state='leased' AND lease_until < ?) ORDER BY id LIMIT 1)
                   RETURNING *""",
                (worker_id, now + LEASE_SECONDS, now),
            ).fetchone()
        return Job.from_row(row) if row else None

    def complete(self, job_id: int, result_path: str, worker_id: str):
        with self.connection() as db:
            return db.execute(
                "UPDATE jobs SET state='done', result_path=?, lease_until=NULL "
                "WHERE id=? AND state='leased' AND worker_id=?",
                (result_path, job_id, worker_id),
            ).rowcount == 1

    def fail(self, job_id: int, error: str, worker_id: str):
        with self.connection() as db:
            return db.execute(
                "UPDATE jobs SET state='failed', error=?, lease_until=NULL "
                "WHERE id=? AND state='leased' AND worker_id=?",
                (error, job_id, worker_id),
            ).rowcount == 1

    def all_done(self):
        with self.connection() as db:
            return db.execute("SELECT COUNT(*) FROM jobs WHERE state != 'done'").fetchone()[0] == 0


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def spine_xhtml(workspace: Path):
    opf_path = workspace / "OEBPS" / "content.opf"
    root = ET.parse(opf_path).getroot()
    manifest = {
        item.attrib["id"]: opf_path.parent / item.attrib["href"]
        for item in root.findall(f".//{OPF_NS}item")
        if item.attrib.get("media-type") == "application/xhtml+xml"
    }
    return [manifest[item.attrib["idref"]] for item in root.findall(f".//{OPF_NS}itemref") if item.attrib["idref"] in manifest]


def iter_blocks(element, locator=""):
    if local_name(element.tag) in TEXT_BLOCKS:
        yield locator
        return
    for index, child in enumerate(element):
        yield from iter_blocks(child, f"{locator}/{index}" if locator else str(index))


def element_at(root, locator: str):
    element = root
    for index in locator.split("/"):
        element = list(element)[int(index)]
    return element


def prepare_jobs(workspace: Path, settings: Settings):
    workspace = Path(workspace)
    inputs = workspace / ".parallel-translate" / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    jobs = []
    for xhtml_path in spine_xhtml(workspace):
        root = ET.parse(xhtml_path).getroot()
        for locator in iter_blocks(root):
            element = element_at(root, locator)
            input_path = inputs / f"{len(jobs) + 1}.xhtml"
            input_path.write_bytes(ET.tostring(element, encoding="utf-8"))
            jobs.append(
                JobDraft(
                    xhtml_path=str(xhtml_path.relative_to(workspace)),
                    locator=locator,
                    input_path=str(input_path),
                    backend=settings.backend,
                    model=settings.model,
                    target_language=settings.target_language,
                )
            )
    return jobs


def validate_fragment(source: str, translated: str):
    if not translated.strip():
        raise ValueError("empty result")
    before, after = ET.fromstring(source), ET.fromstring(translated)

    def compare(left, right):
        if left.tag != right.tag or left.attrib != right.attrib or len(left) != len(right):
            raise ValueError("structure changed")
        for left_child, right_child in zip(left, right):
            compare(left_child, right_child)

    compare(before, after)


def choose_from(options, label, input_fn):
    print(label)
    for number, value in enumerate(options, 1):
        print(f"  {number}) {value}")
    print("  0) custom")
    while True:
        choice = input_fn("> ").strip()
        if choice == "0":
            custom = input_fn("Model name: ").strip()
            if custom:
                return custom
        elif choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]
        print("Choose a listed number or 0 for custom.")


def choose_settings(input_fn=input):
    backend = choose_from(("codex", "claude"), "Backend:", input_fn)
    models = CODEX_MODELS if backend == "codex" else CLAUDE_MODELS
    model = choose_from(models, "Model:", input_fn)
    target_language = input_fn("Target language: ").strip()
    while not target_language:
        target_language = input_fn("Target language: ").strip()
    while True:
        workers = input_fn("Workers: ").strip()
        if workers.isdigit() and int(workers) > 0:
            return Settings(backend, model, target_language, int(workers))
        print("Workers must be a positive integer.")


def build_command(job: Job, prompt: str):
    if job.backend == "codex":
        return ["codex", "exec", "--model", job.model, prompt]
    if job.backend == "claude":
        return ["claude", "-p", "--model", job.model, prompt]
    raise ValueError(f"unsupported backend: {job.backend}")


def worker_prompt(job: Job, source: str):
    return (
        f"Translate this XHTML fragment into {job.target_language}. "
        "Return only the translated XHTML fragment. Preserve every tag and attribute exactly.\n\n"
        f"{source}"
    )


def run_worker(store: RunStore, worker_id: str, now=None):
    while True:
        job = store.lease(worker_id, int(time.time()) if now is None else now)
        if job is None:
            return
        source_path = Path(job.input_path)
        try:
            source = source_path.read_text()
            completed = subprocess.run(
                build_command(job, worker_prompt(job, source)),
                capture_output=True,
                text=True,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or f"worker exited {completed.returncode}")
            validate_fragment(source, completed.stdout)
            results = source_path.parent.parent / "results"
            results.mkdir(parents=True, exist_ok=True)
            result_path = results / f"{job.id}.xhtml"
            temporary = result_path.with_suffix(".tmp")
            temporary.write_text(completed.stdout)
            temporary.replace(result_path)
            store.complete(job.id, str(result_path), worker_id)
        except (OSError, RuntimeError, ValueError, ET.ParseError) as error:
            store.fail(job.id, str(error), worker_id)
        if now is not None:
            return


def worker_entry(db_path: str, worker_id: str):
    run_worker(RunStore(Path(db_path)), worker_id)


def replace_at(root, locator: str, replacement):
    parts = locator.split("/")
    parent = root if len(parts) == 1 else element_at(root, "/".join(parts[:-1]))
    index = int(parts[-1])
    parent.remove(list(parent)[index])
    parent.insert(index, replacement)


def merge_run(workspace: Path, store: RunStore, output_epub: Path):
    if not store.all_done():
        raise ValueError("cannot merge unfinished jobs")
    workspace, output_epub = Path(workspace), Path(output_epub)
    staging = workspace / ".parallel-translate" / "merged"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(workspace, staging, ignore=shutil.ignore_patterns(".parallel-translate"))
    language = store.settings().target_language.split("-", 1)[0].lower()
    by_xhtml = {}
    for job in store.done_jobs():
        by_xhtml.setdefault(job.xhtml_path, []).append(job)
    for relative_path, jobs in by_xhtml.items():
        xhtml_path = staging / relative_path
        root = ET.parse(xhtml_path).getroot()
        for job in jobs:
            source = ET.tostring(element_at(root, job.locator), encoding="unicode")
            translated = Path(job.result_path).read_text()
            validate_fragment(source, translated)
            replace_at(root, job.locator, ET.fromstring(translated))
        root.set("lang", language)
        root.set("{http://www.w3.org/XML/1998/namespace}lang", language)
        ET.ElementTree(root).write(xhtml_path, encoding="utf-8", xml_declaration=True)
    output_epub.parent.mkdir(parents=True, exist_ok=True)
    build(staging, output_epub)


def select_book(books, input_fn=input):
    if len(books) == 1:
        return books[0]
    name = choose_from(tuple(path.name for path in books), "Book:", input_fn)
    return next((book for book in books if book.name == name), None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--book", help="EPUB filename inside input/")
    args = parser.parse_args(argv)
    input_dir = args.root / "input"
    if not input_dir.is_dir():
        print(f"missing input directory: {input_dir}")
        return 2
    books = sorted(input_dir.glob("*.epub"))
    if not books:
        print(f"no EPUB files in: {input_dir}")
        return 2
    source = next((book for book in books if book.name == args.book), None) if args.book else select_book(books)
    if source is None:
        print(f"book not found: {args.book}")
        return 2
    workspace = args.root / ".parallel-translate" / source.stem
    store = RunStore(workspace / "run.sqlite")
    if store.has_jobs():
        settings = store.settings()
        print(f"resuming with {settings.backend}/{settings.model}")
    else:
        settings = choose_settings()
        extract(source, workspace)
        drafts = prepare_jobs(workspace, settings)
        if not drafts:
            print("no translatable XHTML blocks found")
            return 2
        store.set_settings(settings)
        store.add_jobs(drafts)
    output = args.root / "output" / f"{source.stem}-{settings.target_language.lower()}.epub"
    if output.exists():
        print(f"output already exists: {output}")
        return 2
    processes = [multiprocessing.Process(target=worker_entry, args=(str(store.db_path), f"worker-{index}")) for index in range(settings.workers)]
    for process in processes:
        process.start()
    for process in processes:
        process.join()
    if not store.all_done():
        print(f"run incomplete: {store.db_path} ({store.failed_count()} failed)")
        return 1
    merge_run(workspace, store, output)
    print(f"built {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
