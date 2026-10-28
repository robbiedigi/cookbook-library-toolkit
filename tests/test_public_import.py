import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cookbook_demo.importer import build, canonical_records, extract_sections, sha, source_bytes
from library_v2.contracts import validate_output
from library_v2.service import SnapshotService

RAW = b'''<html><body><h2>CONTENTS.</h2><p>Do not index this.</p>
<h2 id="start">COOKING.</h2><p>Use a clean pan.</p>
<h3 id="soup">SOUP.</h3><p>Stir <b>1/2 cup</b> water &amp; rice<span class="pagenum"><a id="Page_7">[7]</a></span> gently.</p>
<p>Keep the two batches separate.</p><h3 id="cake">CAKE.</h3><p>Bake the cake.</p>
<h2>STOP.</h2><p>Unrelated household advice.</p></body></html>'''
PROFILE = dict(gutenberg_id="123", title="Synthetic Cookbook", author="Test author", edition="Test edition",
               download_url="https://www.gutenberg.org/example.html", sha256=sha(RAW),
               start_at="COOKING.", end_before="STOP.", recipe_headings=["SOUP.", "CAKE."])


class ImportTests(unittest.TestCase):
    def test_boundaries_normalization_and_citations(self):
        book, recipes, knowledge, extraction = canonical_records(RAW, PROFILE)
        self.assertEqual(len(recipes), 2)
        self.assertEqual(len(knowledge), 3)
        self.assertEqual(recipes[0]["method_blocks"][0]["text"], "Stir 1/2 cup water & rice gently.")
        self.assertEqual(recipes[0]["method_blocks"][1]["text"], "Keep the two batches separate.")
        self.assertEqual(recipes[0]["ingredient_groups"], [])
        self.assertEqual(recipes[0]["dependency_coverage"]["status"], "unknown")
        self.assertEqual(recipes[0]["citations"][0]["document"]["sha256"], sha(RAW))
        self.assertTrue(recipes[0]["citations"][0]["document"]["origin_url"].endswith("#soup"))
        self.assertNotIn("Do not index", json.dumps(knowledge))
        self.assertNotIn("Unrelated household", json.dumps(knowledge))
        self.assertEqual(recipes[0]["citations"][0]["printed_labels"], ["7"])
        self.assertEqual(recipes[0]["citations"][0]["pdf_file_pages"], [])

    def test_changed_or_ambiguous_structure_is_not_silently_imported(self):
        with self.assertRaisesRegex(ValueError, "boundaries"):
            extract_sections(RAW.replace(b"STOP.", b"CHANGED."), PROFILE)
        with self.assertRaisesRegex(ValueError, "missing"):
            canonical_records(RAW, dict(PROFILE, recipe_headings=["MISSING."]))
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            canonical_records(RAW.replace(b"CAKE.", b"SOUP."), dict(PROFILE, recipe_headings=["SOUP."]))
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            extract_sections(RAW.replace(b"<p>Bake the cake.</p>", b"<table><tr><td>cake</td></tr></table>"), PROFILE)

    def test_tampered_cache_and_offline_misses_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                source_bytes(PROFILE, tmp, offline=True)
            Path(tmp, "123.html").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                source_bytes(PROFILE, tmp, offline=True)

    def test_build_reproducible_search_read_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp, patch("cookbook_demo.importer.profiles", return_value=[PROFILE]):
            root = Path(tmp)
            (root / "123.html").write_bytes(RAW)
            first = build(root / "one", root, offline=True)
            second = build(root / "two", root, offline=True)
            self.assertEqual(first["snapshot"], second["snapshot"])
            self.assertEqual((root / "one/recipes.jsonl").read_bytes(), (root / "two/recipes.jsonl").read_bytes())
            self.assertEqual((root / "one/sources/123.html").read_bytes(), RAW)
            with self.assertRaises(FileExistsError):
                build(root / "one", root, offline=True)
            service = SnapshotService(root / "one/library.sqlite", "x" * 32)
            try:
                found = service.search_recipes(query="SOUP")
                validate_output(found)
                self.assertEqual(len(found["items"]), 1)
                read = service.get_recipes([found["items"][0]["id"]])
                validate_output(read)
                self.assertIn("Keep the two batches separate.", json.dumps(read))
                self.assertIn("prose_recipe", json.dumps(read))
                knowledge = service.search_knowledge(query="clean pan")
                self.assertTrue(knowledge["items"])
                validate_output(service.get_passages([knowledge["items"][0]["id"]]))
            finally:
                service.close()

    def test_failed_build_leaves_no_partial_destination(self):
        with tempfile.TemporaryDirectory() as tmp, patch("cookbook_demo.importer.profiles", return_value=[PROFILE]):
            output = Path(tmp) / "new"
            with self.assertRaises(FileNotFoundError):
                build(output, Path(tmp) / "missing", offline=True)
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
