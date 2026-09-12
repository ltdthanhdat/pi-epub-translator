import sys
import tempfile
import unittest
import zipfile
import sqlite3
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from parallel_translate import (
    LEASE_SECONDS,
    JobDraft,
    RunStore,
    Settings,
    build_command,
    choose_settings,
    main,
    merge_run,
    run_worker,
    settings_from_args,
    select_book,
    spine_xhtml,
    prepare_jobs,
    validate_fragment,
    worker_prompt,
)


def job(locator="one"):
    return JobDraft(
        xhtml_path="OEBPS/text/chapter.xhtml",
        locator=locator,
        input_path=f"input/{locator}.xhtml",
        backend="codex",
        model="gpt-5.6-terra",
        target_language="Vietnamese",
    )


def settings():
    return Settings("codex", "default", "Vietnamese", 1)


def write_book(root, body):
    (root / "OEBPS" / "text").mkdir(parents=True)
    (root / "OEBPS" / "content.opf").write_text(
        """<package xmlns="http://www.idpf.org/2007/opf"><manifest>
        <item id="chapter" href="text/chapter.xhtml" media-type="application/xhtml+xml"/>
        </manifest><spine><itemref idref="chapter"/></spine></package>"""
    )
    (root / "OEBPS" / "text" / "chapter.xhtml").write_text(
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><body>' + body + "</body></html>"
    )


class RunStoreTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_expired_lease_is_reissued_once(self):
        store = RunStore(self.tmp / "run.sqlite")
        store.add_jobs([job()])
        first = store.lease("worker-a", now=100)
        self.assertEqual("one", first.locator)
        self.assertIsNone(store.lease("worker-b", now=101))
        second = store.lease("worker-b", now=100 + LEASE_SECONDS + 1)
        self.assertEqual(first.id, second.id)
        self.assertEqual(2, second.attempts)

    def test_connection_context_closes_database(self):
        store = RunStore(self.tmp / "run.sqlite")
        with store.connection() as db:
            db.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")

    def test_complete_cannot_be_completed_twice(self):
        store = RunStore(self.tmp / "run.sqlite")
        store.add_jobs([job()])
        leased = store.lease("worker-a", now=100)
        self.assertTrue(store.complete(leased.id, "results/1.xhtml", "worker-a"))
        self.assertFalse(store.complete(leased.id, "results/1.xhtml", "worker-a"))
        self.assertTrue(store.all_done())

    def test_expired_worker_cannot_complete_released_job(self):
        store = RunStore(self.tmp / "run.sqlite")
        store.add_jobs([job()])
        first = store.lease("worker-a", now=1)
        second = store.lease("worker-b", now=1 + LEASE_SECONDS + 1)
        self.assertFalse(store.complete(first.id, "results/a.xhtml", "worker-a"))
        self.assertTrue(store.complete(second.id, "results/b.xhtml", "worker-b"))

    def test_same_locator_is_allowed_in_different_xhtml_files(self):
        store = RunStore(self.tmp / "run.sqlite")
        store.add_jobs([
            JobDraft("chapter-1.xhtml", "0/0", "input/1.xhtml", "codex", "gpt-5.6-luna", "Vietnamese"),
            JobDraft("chapter-2.xhtml", "0/0", "input/2.xhtml", "codex", "gpt-5.6-luna", "Vietnamese"),
        ])
        self.assertIsNotNone(store.lease("worker", now=1))

    def test_cli_settings_preserve_user_model_and_worker_count(self):
        chosen = settings_from_args("codex", "gpt-5.6-luna", "Vietnamese", "vi", "4", "xhigh")
        self.assertEqual(Settings("codex", "gpt-5.6-luna", "Vietnamese", 4, "xhigh", "vi"), chosen)

    def test_cli_settings_default_to_three_attempts(self):
        chosen = settings_from_args("codex", "gpt-5.6-luna", "Vietnamese", "vi", "4", "xhigh", None)
        self.assertEqual(3, chosen.max_attempts)

    def test_codex_command_passes_reasoning_effort(self):
        leased = RunStore(self.tmp / "run.sqlite")
        leased.add_jobs([job()])
        command = build_command(leased.lease("worker", now=1), "prompt", "xhigh")
        self.assertEqual(["codex", "-c", 'model_reasoning_effort="xhigh"', "exec"], command[:4])

    def test_prepare_keeps_pagebreak_inside_one_block(self):
        write_book(self.tmp, "<section><p>A<span epub:type='pagebreak'/>B</p><p>C</p></section>")
        jobs = prepare_jobs(self.tmp, settings())
        self.assertEqual(2, len(jobs))
        self.assertIn("pagebreak", Path(jobs[0].input_path).read_text())

    def test_prepare_skips_image_only_block(self):
        write_book(self.tmp, "<p class='image'><img alt='cover' src='cover.jpg'/></p><p>Hello</p>")
        jobs = prepare_jobs(self.tmp, settings())
        self.assertEqual(1, len(jobs))

    def test_spine_skips_index_and_notes_but_includes_nav(self):
        (self.tmp / "OEBPS" / "text").mkdir(parents=True)
        (self.tmp / "OEBPS" / "content.opf").write_text(
            "<package xmlns='http://www.idpf.org/2007/opf'><manifest>"
            "<item id='chapter' href='text/chapter.xhtml' media-type='application/xhtml+xml'/>"
            "<item id='index' href='text/Index.xhtml' media-type='application/xhtml+xml'/>"
            "<item id='notes' href='text/Notes.xhtml' media-type='application/xhtml+xml'/>"
            "<item id='nav' href='text/nav.xhtml' media-type='application/xhtml+xml' properties='nav'/>"
            "</manifest><spine><itemref idref='chapter'/><itemref idref='index'/><itemref idref='notes'/></spine></package>"
        )
        for name in ("chapter.xhtml", "Index.xhtml", "Notes.xhtml", "nav.xhtml"):
            (self.tmp / "OEBPS" / "text" / name).write_text("<html/>")
        self.assertEqual(["chapter.xhtml", "nav.xhtml"], [path.name for path in spine_xhtml(self.tmp)])

    def test_worker_restores_empty_pagebreak_marker(self):
        source = self.tmp / "inputs" / "1.xhtml"
        source.parent.mkdir()
        source.write_text("<p>A<span epub:type='pagebreak' xmlns:epub='http://www.idpf.org/2007/ops'/>B</p>")
        store = RunStore(self.tmp / "run.sqlite")
        store.set_settings(Settings("codex", "gpt-5.6-luna", "Vietnamese", 1, "xhigh", "vi"))
        store.add_jobs([JobDraft("chapter.xhtml", "0", str(source), "codex", "gpt-5.6-luna", "Vietnamese")])
        with patch("parallel_translate.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "<p>Đã [[KEEP_PAGEBREAK_1_0]] dịch</p>"
            run.return_value.stderr = ""
            run_worker(store, "worker", now=1)
        with store.connection() as db:
            self.assertEqual("done", db.execute("SELECT state FROM jobs").fetchone()["state"])

    def test_worker_retries_failed_fragment_until_valid(self):
        source = self.tmp / "inputs" / "1.xhtml"
        source.parent.mkdir()
        source.write_text("<p>Hello</p>")
        store = RunStore(self.tmp / "run.sqlite")
        store.set_settings(Settings("codex", "gpt-5.6-luna", "Vietnamese", 1, "xhigh", "vi", max_attempts=2))
        store.add_jobs([JobDraft("chapter.xhtml", "0", str(source), "codex", "gpt-5.6-luna", "Vietnamese")])
        with patch("parallel_translate.subprocess.run") as run:
            run.side_effect = [
                type("Result", (), {"returncode": 0, "stdout": "<p><i>bad</i></p>", "stderr": ""})(),
                type("Result", (), {"returncode": 0, "stdout": "<p>Xin chào</p>", "stderr": ""})(),
            ]
            run_worker(store, "worker")
        self.assertTrue(store.all_done())

    def test_resume_can_raise_worker_count_without_recreating_jobs(self):
        store = RunStore(self.tmp / "run.sqlite")
        store.set_settings(Settings("codex", "gpt-5.6-luna", "Vietnamese", 4, "xhigh", "vi"))
        store.add_jobs([job()])
        store.update_workers(20)
        self.assertEqual(20, store.settings().workers)
        self.assertTrue(store.has_jobs())

    def test_prompt_includes_glossary(self):
        draft = JobDraft("chapter.xhtml", "0", "input/1.xhtml", "codex", "gpt-5.6-luna", "Vietnamese")
        leased = RunStore(self.tmp / "run.sqlite")
        leased.add_jobs([draft])
        prompt = worker_prompt(leased.lease("worker", now=1), "<p>Vietnam</p>", "Vietnam=Việt Nam")
        self.assertIn("Vietnam=Việt Nam", prompt)

    def test_validate_fragment_rejects_changed_href(self):
        with self.assertRaisesRegex(ValueError, "structure"):
            validate_fragment('<p><a href="n">text</a></p>', '<p><a href="x">dịch</a></p>')

    def test_choose_settings_accepts_exact_model_name_and_language_code(self):
        answers = iter(["1", "gpt-5.6-luna", "4", "Vietnamese", "vi", "3"])
        chosen = choose_settings(lambda _: next(answers))
        self.assertEqual("codex", chosen.backend)
        self.assertEqual("gpt-5.6-luna", chosen.model)
        self.assertEqual("vi", chosen.target_language_code)
        self.assertEqual(3, chosen.workers)

    def test_build_command_uses_noninteractive_model_flag(self):
        leased = RunStore(self.tmp / "run.sqlite")
        leased.add_jobs([job()])
        command = build_command(leased.lease("worker", now=1), "prompt")
        self.assertEqual(["codex", "exec", "--model", "gpt-5.6-terra", "prompt"], command)

    def test_worker_writes_valid_result_without_touching_input(self):
        source = self.tmp / "inputs" / "1.xhtml"
        source.parent.mkdir()
        source.write_text("<p>Hello</p>")
        store = RunStore(self.tmp / "run.sqlite")
        store.set_settings(Settings("codex", "gpt-5.6-terra", "Vietnamese", 1, "xhigh"))
        store.add_jobs([JobDraft("chapter.xhtml", "0", str(source), "codex", "gpt-5.6-terra", "Vietnamese")])
        with patch("parallel_translate.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "<p>Xin chào</p>"
            run.return_value.stderr = ""
            run_worker(store, "worker", now=1)
        with store.connection() as db:
            row = db.execute("SELECT state, result_path FROM jobs").fetchone()
        self.assertEqual("done", row["state"])
        self.assertEqual("<p>Hello</p>", source.read_text())
        self.assertEqual("<p>Xin chào</p>", Path(row["result_path"]).read_text())

    def test_merge_builds_output_without_modifying_extracted_source(self):
        write_book(self.tmp, "<p>Hello</p><p>World</p>")
        (self.tmp / "mimetype").write_text("application/epub+zip")
        drafts = prepare_jobs(self.tmp, settings())
        store = RunStore(self.tmp / "run.sqlite")
        store.set_settings(Settings("codex", "default", "Vietnamese", 1, target_language_code="vi"))
        store.add_jobs(drafts)
        for number, draft in enumerate(drafts, 1):
            source = Path(draft.input_path).read_text()
            result = self.tmp / "results" / f"{number}.xhtml"
            result.parent.mkdir(exist_ok=True)
            result.write_text(source.replace("Hello", "Xin chào"))
            leased = store.lease(f"worker-{number}", now=number)
            store.complete(leased.id, str(result), f"worker-{number}")
        original = (self.tmp / "OEBPS" / "text" / "chapter.xhtml").read_text()
        output = self.tmp / "output" / "translated.epub"
        merge_run(self.tmp, store, output)
        self.assertTrue(output.is_file())
        self.assertEqual(original, (self.tmp / "OEBPS" / "text" / "chapter.xhtml").read_text())
        with zipfile.ZipFile(output) as epub:
            self.assertNotIn("run.sqlite", epub.namelist())
            root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(epub.read("OEBPS/text/chapter.xhtml"))
        self.assertEqual("vi", root.attrib["lang"])

    def test_merge_verifies_workspace_below_parallel_translate_directory(self):
        workspace = self.tmp / ".parallel-translate" / "book"
        write_book(workspace, "<p>Hello</p>")
        (workspace / "mimetype").write_text("application/epub+zip")
        drafts = prepare_jobs(workspace, settings())
        store = RunStore(workspace / "run.sqlite")
        store.set_settings(Settings("codex", "default", "Vietnamese", 1, target_language_code="vi"))
        store.add_jobs(drafts)
        for number, draft in enumerate(drafts, 1):
            result = workspace / "results" / f"{number}.xhtml"
            result.parent.mkdir(exist_ok=True)
            result.write_text(Path(draft.input_path).read_text().replace("Hello", "Xin chào"))
            leased = store.lease(f"worker-{number}", now=number)
            store.complete(leased.id, str(result), f"worker-{number}")
        output = self.tmp / "output" / "translated.epub"
        merge_run(workspace, store, output)
        self.assertTrue(output.is_file())

    def test_main_rejects_missing_input_directory(self):
        self.assertEqual(2, main(["--root", str(self.tmp)]))

    def test_main_rejects_scope_change_on_resume(self):
        input_dir = self.tmp / "input"
        input_dir.mkdir()
        (input_dir / "book.epub").write_bytes(b"")
        workspace = self.tmp / ".parallel-translate" / "book"
        store = RunStore(workspace / "run.sqlite")
        store.set_settings(settings())
        store.add_jobs([job()])
        with self.assertRaises(SystemExit) as error:
            main(["--root", str(self.tmp), "--book", "book.epub", "--translate-index"])
        self.assertEqual(2, error.exception.code)

    def test_select_book_uses_numbered_menu(self):
        books = [self.tmp / "a.epub", self.tmp / "b.epub"]
        self.assertEqual(books[1], select_book(books, lambda _: "2"))


if __name__ == "__main__":
    unittest.main()
