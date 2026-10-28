import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from cookbook_tools.pipelines import import_epub, prepare_hybrid, project_pdf, read_rows, review_pdf
from scripts.make_toolkit_examples import make

ROOT = Path(__file__).resolve().parents[1]


class ToolkitTests(unittest.TestCase):
    def test_epub_custom_profile_preserves_source_and_holds_incomplete_recipe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make(root / "examples")
            source, profile = root / "examples/example.epub", root / "examples/epub-profile.json"
            import_epub(source, profile, root / "import")
            records = read_rows(root / "import/recipes.jsonl")
            self.assertEqual(records[0]["ingredients"], ["2 carrots", "1 cup water"])
            self.assertEqual(records[0]["instructions"], ["Simmer until tender."])
            self.assertEqual(len(read_rows(root / "import/held-recipes.jsonl")), 1)
            self.assertEqual((root / "import/sources/example.epub").read_bytes(), source.read_bytes())
            source.write_bytes(source.read_bytes() + b"changed")
            with self.assertRaisesRegex(ValueError, "edition profile"):
                import_epub(source, profile, root / "bad")
            self.assertFalse((root / "bad").exists())

    def test_pdf_retained_evidence_to_reviewed_candidate_and_stale_render_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make(root / "examples")
            examples = root / "examples"
            review_pdf(examples / "ocr-evidence", examples / "pdf-profile.json", root / "review")
            args = [sys.executable, "-m", "scripts.assemble_reviewed_pdf", "--source-bundle", str(root / "review"),
                    "--ledger", str(examples / "review-ledger.json"), "--output", str(root / "assembled")]
            completed = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = project_pdf(root / "assembled", root / "candidate")
            self.assertEqual(result["recipes"], 1)
            self.assertIsNone(result["personally_tested"])
            recipe = read_rows(root / "candidate/recipes.jsonl")[0]
            self.assertEqual(recipe["ingredients"], ["2 carrots"])
            pages = read_rows(root / "candidate/review-evidence/page-evidence.jsonl")
            self.assertEqual(pages[0]["raw_page"]["cells"][1]["text"], "2 carrots")
            self.assertEqual(pages[0]["printed_page"], "7")
            with self.assertRaisesRegex(ValueError, "outside"):
                project_pdf(root / "assembled", root / "assembled/nested")
            (examples / "synthetic-page.svg").write_text("changed")
            failed = subprocess.run(args[:-1] + [str(root / "bad")], capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("Source render changed", failed.stderr)
            self.assertFalse((root / "bad").exists())

    def test_personal_snapshot_projects_stable_ids_without_changing_source(self):
        from library_v2.personal import build
        from library_v2.store import Snapshot
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build(ROOT / "examples/personal", root / "library")
            original = (root / "library/library.sqlite").read_bytes()
            report = prepare_hybrid(root / "library", root / "hybrid")
            self.assertEqual(report["recipes"], 1)
            rows = read_rows(root / "hybrid/recipes.jsonl")
            self.assertEqual(rows[0]["ingredients"][0], "1 cup rice")
            snapshot = Snapshot(root / "library/library.sqlite")
            try:
                ids = [row[0] for row in snapshot.db.execute("SELECT id FROM recipes")]
                self.assertEqual([row["id"] for row in rows], ids)
            finally:
                snapshot.close()
            self.assertEqual(original, (root / "library/library.sqlite").read_bytes())
