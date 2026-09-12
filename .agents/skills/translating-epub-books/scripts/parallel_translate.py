#!/usr/bin/env python3
"""Run resumable parallel EPUB translation jobs."""

import argparse
from contextlib import contextmanager
import multiprocessing
import shutil
import sqlite3
import subprocess
import time
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
import xml.etree.ElementTree as ET

from epub_tool import build, extract


LEASE_SECONDS = 900
OPF_NS = "{http://www.idpf.org/2007/opf}"
TEXT_BLOCKS = {"p", "li", "td", "th", "caption", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6"}
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"


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
    reasoning_effort: str = ""
    target_language_code: str = ""
    glossary_path: str = ""
    max_attempts: int = 3
    translate_index: bool = False
    translate_notes: bool = False


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
                    locator TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    result_path TEXT,
                    backend TEXT NOT NULL,
                    model TEXT NOT NULL,
                    target_language TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    worker_id TEXT,
                    lease_until INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    UNIQUE(xhtml_path, locator)
                );
                """
            )
            self._migrate_locator_unique(db)

    @staticmethod
    def _migrate_locator_unique(db):
        for index in db.execute("PRAGMA index_list(jobs)").fetchall():
            if not index["unique"]:
                continue
            columns = [row["name"] for row in db.execute(f"PRAGMA index_info({index['name']})")]
            if columns != ["locator"]:
                continue
            db.executescript(
                """
                CREATE TABLE jobs_new (
                    id INTEGER PRIMARY KEY, xhtml_path TEXT NOT NULL, locator TEXT NOT NULL,
                    input_path TEXT NOT NULL, result_path TEXT, backend TEXT NOT NULL,
                    model TEXT NOT NULL, target_language TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', worker_id TEXT,
                    lease_until INTEGER, attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
                    UNIQUE(xhtml_path, locator)
                );
                INSERT INTO jobs_new SELECT * FROM jobs;
                DROP TABLE jobs;
                ALTER TABLE jobs_new RENAME TO jobs;
                """
            )
            return

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

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
                 ("target_language", settings.target_language), ("workers", str(settings.workers)),
                 ("reasoning_effort", settings.reasoning_effort),
                 ("target_language_code", settings.target_language_code),
                 ("glossary_path", settings.glossary_path),
                 ("max_attempts", str(settings.max_attempts)),
                 ("translate_index", str(settings.translate_index)),
                 ("translate_notes", str(settings.translate_notes))],
            )

    def settings(self):
        with self.connection() as db:
            values = dict(db.execute("SELECT key, value FROM runs"))
        return Settings(
            values["backend"], values["model"], values["target_language"], int(values["workers"]),
            values.get("reasoning_effort", ""), values.get("target_language_code") or language_code(values["target_language"]),
            values.get("glossary_path", ""), int(values.get("max_attempts", "3")),
            values.get("translate_index", "False") == "True", values.get("translate_notes", "False") == "True",
        )

    def update_workers(self, workers: int):
        if workers < 1:
            raise ValueError("workers must be a positive integer")
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO runs(key, value) VALUES ('workers', ?)", (str(workers),))

    def update_max_attempts(self, max_attempts: int):
        if max_attempts < 1:
            raise ValueError("max attempts must be a positive integer")
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO runs(key, value) VALUES ('max_attempts', ?)", (str(max_attempts),))

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

    def lease(self, worker_id: str, now: int, max_attempts=3):
        with self.connection() as db:
            row = db.execute(
                """UPDATE jobs SET state='leased', worker_id=?, lease_until=?,
                   attempts=attempts+1
                   WHERE id=(SELECT id FROM jobs WHERE state='pending' OR
                     (state='leased' AND lease_until < ?) OR
                     (state='failed' AND attempts < ?) ORDER BY id LIMIT 1)
                   RETURNING *""",
                (worker_id, now + LEASE_SECONDS, now, max_attempts),
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


def language_code(language: str):
    common = {"vietnamese": "vi", "english": "en", "french": "fr", "spanish": "es", "german": "de", "japanese": "ja", "chinese": "zh", "korean": "ko"}
    normalized = language.strip().lower()
    return common.get(normalized, normalized if normalized.isalpha() and 2 <= len(normalized) <= 3 else "")


def spine_xhtml(workspace: Path, translate_index=False, translate_notes=False):
    opf_path = workspace / "OEBPS" / "content.opf"
    root = ET.parse(opf_path).getroot()
    manifest = {
        item.attrib["id"]: (opf_path.parent / item.attrib["href"], item.attrib.get("properties", ""))
        for item in root.findall(f".//{OPF_NS}item")
        if item.attrib.get("media-type") == "application/xhtml+xml"
    }
    files = [manifest[item.attrib["idref"]][0] for item in root.findall(f".//{OPF_NS}itemref") if item.attrib["idref"] in manifest]
    excluded = set()
    if not translate_index:
        excluded.add("Index.xhtml")
    if not translate_notes:
        excluded.add("Notes.xhtml")
    files = [path for path in files if path.name not in excluded]
    nav = next((path for path, properties in manifest.values() if "nav" in properties.split()), None)
    return files + ([nav] if nav and nav not in files else [])


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
    for xhtml_path in spine_xhtml(workspace, settings.translate_index, settings.translate_notes):
        root = ET.parse(xhtml_path).getroot()
        for locator in iter_blocks(root):
            element = element_at(root, locator)
            if not "".join(element.itertext()).strip():
                continue
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


def protect_empty_pagebreaks(source: str, job_id: int):
    root = ET.fromstring(source)
    markers, number = {}, 0
    for parent in root.iter():
        index = 0
        while index < len(parent):
            child = parent[index]
            if local_name(child.tag) == "span" and child.attrib.get(EPUB_TYPE) == "pagebreak" and not (child.text or "").strip() and not len(child):
                marker = f"[[KEEP_PAGEBREAK_{job_id}_{number}]]"
                number += 1
                tail, child.tail = child.tail or "", None
                markers[marker] = ET.tostring(child, encoding="unicode")
                if index:
                    parent[index - 1].tail = (parent[index - 1].tail or "") + marker + tail
                else:
                    parent.text = (parent.text or "") + marker + tail
                parent.remove(child)
                continue
            index += 1
    return ET.tostring(root, encoding="unicode"), markers


def restore_pagebreaks(translated: str, markers: dict[str, str]):
    for marker, markup in markers.items():
        if translated.count(marker) != 1:
            raise ValueError("pagebreak marker changed")
        translated = translated.replace(marker, markup)
    return translated


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
    model = input_fn("Model name: ").strip()
    while not model:
        model = input_fn("Model name: ").strip()
    effort = ""
    if backend == "codex":
        effort = choose_from(("low", "medium", "high", "xhigh", "max"), "Reasoning effort:", input_fn)
    target_language = input_fn("Target language: ").strip()
    while not target_language:
        target_language = input_fn("Target language: ").strip()
    target_language_code = input_fn("Target language code (BCP-47): ").strip().lower()
    while not target_language_code:
        target_language_code = input_fn("Target language code (BCP-47): ").strip().lower()
    while True:
        workers = input_fn("Workers: ").strip()
        if workers.isdigit() and int(workers) > 0:
            return Settings(backend, model, target_language, int(workers), effort, target_language_code)
        print("Workers must be a positive integer.")


def settings_from_args(backend: str, model: str, target_language: str, target_language_code: str, workers: str, reasoning_effort: str | None, max_attempts="3"):
    if backend not in ("codex", "claude") or not model or not target_language or not target_language_code:
        raise ValueError("backend, model, target language, and target language code are required")
    if not workers.isdigit() or int(workers) < 1:
        raise ValueError("workers must be a positive integer")
    if backend == "codex" and reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError("Codex reasoning effort must be low, medium, high, xhigh, or max")
    max_attempts = max_attempts or "3"
    if not max_attempts.isdigit() or int(max_attempts) < 1:
        raise ValueError("max attempts must be a positive integer")
    return Settings(backend, model, target_language, int(workers), reasoning_effort or "", target_language_code.lower(), max_attempts=int(max_attempts))


def build_command(job: Job, prompt: str, reasoning_effort=""):
    if job.backend == "codex":
        command = ["codex"]
        if reasoning_effort:
            command += ["-c", f'model_reasoning_effort="{reasoning_effort}"']
        return command + ["exec", "--model", job.model, prompt]
    if job.backend == "claude":
        return ["claude", "-p", "--model", job.model, prompt]
    raise ValueError(f"unsupported backend: {job.backend}")


def worker_prompt(job: Job, source: str, glossary=""):
    return (
        (f"Apply this glossary consistently:\n{glossary}\n\n" if glossary else "")
        + f"Translate this XHTML fragment into {job.target_language}. "
        "Return only the translated XHTML fragment. Preserve every tag and attribute exactly. "
        "Copy every [[KEEP_PAGEBREAK_*]] marker exactly once and do not translate it.\n\n"
        f"{source}"
    )


def run_worker(store: RunStore, worker_id: str, now=None):
    while True:
        job = store.lease(worker_id, int(time.time()) if now is None else now, store.settings().max_attempts)
        if job is None:
            return
        source_path = Path(job.input_path)
        try:
            source = source_path.read_text()
            protected, markers = protect_empty_pagebreaks(source, job.id)
            glossary_path = Path(store.settings().glossary_path) if store.settings().glossary_path else None
            glossary = glossary_path.read_text() if glossary_path else ""
            completed = subprocess.run(
                build_command(job, worker_prompt(job, protected, glossary), store.settings().reasoning_effort),
                capture_output=True,
                text=True,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or f"worker exited {completed.returncode}")
            translated = restore_pagebreaks(completed.stdout, markers)
            validate_fragment(source, translated)
            results = source_path.parent.parent / "results"
            results.mkdir(parents=True, exist_ok=True)
            result_path = results / f"{job.id}.xhtml"
            temporary = result_path.with_suffix(".tmp")
            temporary.write_text(translated)
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
    shutil.copytree(workspace, staging, ignore=shutil.ignore_patterns(".parallel-translate", "run.sqlite", "run.sqlite-shm", "run.sqlite-wal"))
    language = store.settings().target_language_code
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
    verify_output(workspace, store, output_epub)


def verify_output(workspace: Path, store: RunStore, output_epub: Path):
    workspace = Path(workspace)
    with zipfile.ZipFile(output_epub) as epub:
        if epub.testzip() is not None or not epub.infolist() or epub.infolist()[0].filename != "mimetype" or epub.infolist()[0].compress_type != zipfile.ZIP_STORED:
            raise ValueError("invalid EPUB archive")
        expected = {
            relative.as_posix()
            for path in workspace.rglob("*")
            if path.is_file()
            for relative in [path.relative_to(workspace)]
            if relative.name not in {"run.sqlite", "run.sqlite-shm", "run.sqlite-wal"}
            and (not relative.parts or relative.parts[0] != ".parallel-translate")
        }
        try:
            expected.discard(output_epub.relative_to(workspace).as_posix())
        except ValueError:
            pass
        if set(epub.namelist()) != expected:
            raise ValueError("EPUB members changed unexpectedly")
        for relative_path in {job.xhtml_path for job in store.done_jobs()}:
            if b"KEEP_PAGEBREAK_" in epub.read(relative_path):
                raise ValueError("pagebreak marker leaked into EPUB")


def select_book(books, input_fn=input):
    if len(books) == 1:
        return books[0]
    name = choose_from(tuple(path.name for path in books), "Book:", input_fn)
    return next((book for book in books if book.name == name), None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--book", help="EPUB filename inside input/")
    parser.add_argument("--backend", choices=("codex", "claude"))
    parser.add_argument("--model")
    parser.add_argument("--target-language")
    parser.add_argument("--target-language-code")
    parser.add_argument("--workers")
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh", "max"))
    parser.add_argument("--max-attempts")
    parser.add_argument("--glossary", type=Path)
    parser.add_argument("--translate-index", action="store_true")
    parser.add_argument("--translate-notes", action="store_true")
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
        if args.glossary:
            parser.error("glossary is fixed for an existing run; start a new run to change it")
        if args.translate_index or args.translate_notes:
            parser.error("translation scope is fixed for an existing run; start a new run to change it")
        if args.workers:
            try:
                store.update_workers(int(args.workers))
            except ValueError as error:
                parser.error(str(error))
        if args.max_attempts:
            try:
                store.update_max_attempts(int(args.max_attempts))
            except ValueError:
                parser.error("max attempts must be a positive integer")
        settings = store.settings()
        print(f"resuming with {settings.backend}/{settings.model}")
    else:
        values = (args.backend, args.model, args.target_language, args.target_language_code, args.workers, args.reasoning_effort)
        if any(value is not None for value in values):
            try:
                settings = settings_from_args(*values, args.max_attempts)
            except ValueError as error:
                parser.error(str(error))
        else:
            settings = choose_settings()
        extract(source, workspace)
        if args.glossary:
            if not args.glossary.is_file():
                parser.error(f"glossary not found: {args.glossary}")
            glossary = workspace / ".parallel-translate" / "glossary.txt"
            glossary.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.glossary, glossary)
            settings = replace(settings, glossary_path=str(glossary))
        settings = replace(settings, translate_index=args.translate_index, translate_notes=args.translate_notes)
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
