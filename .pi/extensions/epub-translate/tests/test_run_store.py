import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from run_store import (
    LEASE_SECONDS,
    RunStore,
    claim_payload,
    complete_translation,
    glossary_candidates,
    inspect_documents,
    merge_run,
    prepare_run,
    sample_documents,
)


class RunStoreLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tempdir.name)
        self.store = RunStore(self.tmp / "run.sqlite")
        self.store.create_run(
            {
                "model": "test/model",
                "thinking": "high",
                "target_language": "Vietnamese",
                "target_language_code": "vi",
                "max_attempts": 2,
            }
        )
        self.store.add_jobs(
            [
                {
                    "xhtml_path": "OEBPS/text/chapter.xhtml",
                    "locator": "0",
                    "input_path": "inputs/1.xhtml",
                },
                {
                    "xhtml_path": "OEBPS/text/chapter.xhtml",
                    "locator": "1",
                    "input_path": "inputs/2.xhtml",
                },
            ]
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_claim_returns_distinct_jobs_for_concurrent_workers(self):
        first = self.store.claim("worker-a", now=100)
        second = self.store.claim("worker-b", now=100)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(1, first.attempts)
        self.assertEqual(1, second.attempts)

    def test_expired_lease_is_reissued_with_new_token(self):
        first = self.store.claim("worker-a", now=100)
        second = self.store.claim("worker-b", now=100 + LEASE_SECONDS + 1)

        self.assertEqual(first.id, second.id)
        self.assertNotEqual(first.lease_token, second.lease_token)
        self.assertEqual(2, second.attempts)

    def test_heartbeat_requires_current_worker_and_token(self):
        claimed = self.store.claim("worker-a", now=100)

        self.assertFalse(self.store.heartbeat(claimed.id, "worker-b", claimed.lease_token, now=110))
        self.assertFalse(self.store.heartbeat(claimed.id, "worker-a", "stale-token", now=110))
        self.assertTrue(self.store.heartbeat(claimed.id, "worker-a", claimed.lease_token, now=110))

    def test_stale_worker_cannot_complete_after_reclaim(self):
        old = self.store.claim("worker-a", now=100)
        current = self.store.claim("worker-b", now=100 + LEASE_SECONDS + 1)

        self.assertFalse(self.store.complete(old.id, "worker-a", old.lease_token, "results/old.xhtml"))
        self.assertTrue(self.store.complete(current.id, "worker-b", current.lease_token, "results/current.xhtml"))

    def test_cancel_blocks_new_claims_and_retry_resets_only_failed_jobs(self):
        claimed = self.store.claim("worker-a", now=100)
        self.assertTrue(self.store.fail(claimed.id, "worker-a", claimed.lease_token, "bad translation"))
        self.assertTrue(self.store.request_cancel())
        self.assertIsNone(self.store.claim("worker-b", now=101))

        self.assertTrue(self.store.clear_cancel())
        self.assertEqual(1, self.store.retry_failed())
        retried = self.store.claim("worker-c", now=102)
        self.assertEqual(claimed.id, retried.id)
        self.assertEqual(1, retried.attempts)

    def test_only_one_worker_can_claim_merge(self):
        first = self.store.claim("worker-a", now=100)
        second = self.store.claim("worker-b", now=100)
        self.assertTrue(self.store.complete(first.id, "worker-a", first.lease_token, "results/1.xhtml"))
        self.assertTrue(self.store.complete(second.id, "worker-b", second.lease_token, "results/2.xhtml"))

        self.assertTrue(self.store.try_claim_merge("worker-a"))
        self.assertFalse(self.store.try_claim_merge("worker-b"))

    def test_inspect_documents_reports_semantics_without_filename_exclusions(self):
        source = self.tmp / "source"
        write_fixture_epub(source)

        documents = inspect_documents(source)
        by_path = {document.path: document for document in documents}

        self.assertEqual({"OEBPS/text/chapter.xhtml", "OEBPS/text/notes-weird.xhtml"}, set(by_path))
        self.assertIn("footnote", by_path["OEBPS/text/notes-weird.xhtml"].epub_types)
        self.assertTrue(by_path["OEBPS/text/chapter.xhtml"].in_spine)
        self.assertEqual("Chapter One", by_path["OEBPS/text/chapter.xhtml"].title)

    def test_glossary_candidates_filter_noise_and_rank_repeated_names(self):
        candidates = glossary_candidates([
            "Nguyen Van A met Nguyen Van A. https://example.test 42",
            "Nguyen Van A returned to Hanoi.",
        ])

        self.assertEqual("Nguyen Van A", candidates[0].term)
        self.assertGreaterEqual(candidates[0].frequency, 2)
        self.assertNotIn("https", " ".join(candidate.term for candidate in candidates))
        self.assertNotIn("42", " ".join(candidate.term for candidate in candidates))

    def test_prepare_run_extracts_source_snapshots_glossary_and_creates_jobs(self):
        source_epub = self.tmp / "book.epub"
        source_dir = self.tmp / "book-source"
        write_fixture_epub(source_dir)
        import zipfile
        with zipfile.ZipFile(source_epub, "w") as archive:
            archive.write(source_dir / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)
            for path in sorted(source_dir.rglob("*")):
                if path.is_file() and path.name != "mimetype":
                    archive.write(path, path.relative_to(source_dir).as_posix())

        run_dir = self.tmp / "run"
        summary = prepare_run(
            source_epub,
            run_dir,
            {
                "model": "test/model",
                "thinking": "high",
                "target_language": "Vietnamese",
                "target_language_code": "vi",
                "glossary_text": "Hanoi = Hà Nội",
                "output_path": str(self.tmp / "output" / "translated.epub"),
            },
            ["OEBPS/text/chapter.xhtml"],
        )

        self.assertEqual(2, summary["total"])
        self.assertEqual("Hanoi = Hà Nội", (run_dir / "glossary.txt").read_text())
        self.assertTrue((run_dir / "source" / "OEBPS/text/chapter.xhtml").is_file())
        self.assertEqual(1, len(sample_documents(run_dir / "source", ["OEBPS/text/chapter.xhtml"], 2)))

    def test_complete_translation_restores_pagebreak_and_fences_result(self):
        run_dir = self.tmp / "run"
        source = run_dir / "source"
        source.mkdir(parents=True)
        input_path = run_dir / "inputs" / "1.xhtml"
        input_path.parent.mkdir()
        raw = "<p xmlns='http://www.w3.org/1999/xhtml'>A<span xmlns:epub='http://www.idpf.org/2007/ops' epub:type='pagebreak'/>B</p>"
        input_path.write_text(raw)
        store = RunStore(run_dir / "run.sqlite")
        (run_dir / "glossary.txt").write_text("Hanoi = Hà Nội")
        store.create_run({"target_language_code": "vi", "glossary_path": str(run_dir / "glossary.txt")})
        store.add_jobs([{"xhtml_path": "chapter.xhtml", "locator": "0", "input_path": str(input_path)}])
        claimed = store.claim("worker", now=100)
        payload = claim_payload(run_dir, claimed)
        translated = payload["source"].replace(">A", ">Đã")

        result = complete_translation(run_dir, claimed.id, "worker", claimed.lease_token, translated)

        self.assertTrue(result["accepted"])
        result_path = Path(result["result_path"])
        self.assertIn("pagebreak", result_path.read_text())
        self.assertTrue(store.summary()["done"] == 1)

    def test_merge_builds_verified_epub_without_modifying_source(self):
        source = self.tmp / "source"
        write_fixture_epub(source)
        run_dir = self.tmp / "run"
        run_dir.mkdir()
        (run_dir / "run.sqlite").touch()
        store = RunStore(run_dir / "run.sqlite")
        store.create_run({
            "target_language_code": "vi",
            "selected_paths": ["OEBPS/text/chapter.xhtml"],
            "output_path": str(self.tmp / "output" / "translated.epub"),
        })
        (run_dir / "source").parent.mkdir(exist_ok=True)
        import shutil
        shutil.copytree(source, run_dir / "source")
        input_path = run_dir / "inputs" / "1.xhtml"
        input_path.parent.mkdir()
        input_path.write_text("<p>Hello</p>")
        store.add_jobs([{
            "xhtml_path": "OEBPS/text/chapter.xhtml",
            "locator": "1/1",
            "input_path": str(input_path),
        }])
        claimed = store.claim("worker", now=100)
        result_path = run_dir / "results" / "1.xhtml"
        result_path.parent.mkdir()
        result_path.write_text("<p xmlns='http://www.w3.org/1999/xhtml'>Xin chào</p>")
        self.assertTrue(store.complete(claimed.id, "worker", claimed.lease_token, str(result_path)))
        self.assertTrue(store.try_claim_merge("worker"))

        output = merge_run(run_dir)

        self.assertTrue(output.is_file())
        self.assertEqual("<p>Hello</p>", input_path.read_text())
        with __import__("zipfile").ZipFile(output) as epub:
            self.assertEqual("mimetype", epub.infolist()[0].filename)
            self.assertEqual(__import__("zipfile").ZIP_STORED, epub.infolist()[0].compress_type)
            self.assertIn("Xin chào".encode(), epub.read("OEBPS/text/chapter.xhtml"))


def write_fixture_epub(root: Path):
    (root / "META-INF").mkdir(parents=True)
    (root / "OEBPS" / "text").mkdir(parents=True)
    (root / "mimetype").write_text("application/epub+zip")
    (root / "META-INF" / "container.xml").write_text(
        "<container xmlns='urn:oasis:names:tc:opendocument:xmlns:container' version='1.0'>"
        "<rootfiles><rootfile full-path='OEBPS/content.opf' "
        "media-type='application/oebps-package+xml'/></rootfiles></container>"
    )
    (root / "OEBPS" / "content.opf").write_text(
        "<package xmlns='http://www.idpf.org/2007/opf'>"
        "<manifest>"
        "<item id='chapter' href='text/chapter.xhtml' media-type='application/xhtml+xml'/>"
        "<item id='notes' href='text/notes-weird.xhtml' media-type='application/xhtml+xml'/>"
        "</manifest><spine><itemref idref='chapter'/></spine></package>"
    )
    (root / "OEBPS" / "text" / "chapter.xhtml").write_text(
        "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Chapter One</title></head>"
        "<body><h1>Chapter One</h1><p>Hello</p></body></html>"
    )
    (root / "OEBPS" / "text" / "notes-weird.xhtml").write_text(
        "<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'>"
        "<body><section epub:type='footnote'><p>Note</p></section></body></html>"
    )


if __name__ == "__main__":
    unittest.main()
