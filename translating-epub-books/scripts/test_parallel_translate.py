import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from parallel_translate import (
    LEASE_SECONDS,
    JobDraft,
    RunStore,
    Settings,
    CODEX_MODELS,
    build_command,
    choose_settings,
    main,
    merge_run,
    run_worker,
    select_book,
    prepare_jobs,
    validate_fragment,
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

    def test_prepare_keeps_pagebreak_inside_one_block(self):
        write_book(self.tmp, "<section><p>A<span epub:type='pagebreak'/>B</p><p>C</p></section>")
        jobs = prepare_jobs(self.tmp, settings())
        self.assertEqual(2, len(jobs))
        self.assertIn("pagebreak", Path(jobs[0].input_path).read_text())

    def test_validate_fragment_rejects_changed_href(self):
        with self.assertRaisesRegex(ValueError, "structure"):
            validate_fragment('<p><a href="n">text</a></p>', '<p><a href="x">dịch</a></p>')

    def test_choose_settings_accepts_numbered_model(self):
        answers = iter(["1", "2", "Vietnamese", "3"])
        chosen = choose_settings(lambda _: next(answers))
        self.assertEqual("codex", chosen.backend)
        self.assertEqual(CODEX_MODELS[1], chosen.model)
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
        store.set_settings(settings())
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

    def test_main_rejects_missing_input_directory(self):
        self.assertEqual(2, main(["--root", str(self.tmp)]))

    def test_select_book_uses_numbered_menu(self):
        books = [self.tmp / "a.epub", self.tmp / "b.epub"]
        self.assertEqual(books[1], select_book(books, lambda _: "2"))


if __name__ == "__main__":
    unittest.main()
