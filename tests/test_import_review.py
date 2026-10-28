import copy
import unittest

from cookbook.import_review import pending_import_review


class ImportReviewTests(unittest.TestCase):
    def test_extracted_links_or_review_labels_cannot_claim_dependency_acceptance(self):
        rows = [dict(id='pie', book_id='new', ingredients=['prepared pastry'],
                     dependencies=[], review={'status': 'accepted'})]
        original = copy.deepcopy(rows)
        plan = pending_import_review(rows, {'new': 'a' * 64})
        self.assertFalse(plan['acceptance_claimed'])
        self.assertEqual(plan['books'][0]['dependency_coverage'], 'unreviewed')
        self.assertEqual(plan['books'][0]['visual_source_review'], 'pending')
        self.assertEqual(rows, original)

    def test_empty_extraction_still_requires_review(self):
        plan = pending_import_review([], {'held-book': 'b' * 64})
        self.assertEqual(plan['books'][0]['imported_record_count'], 0)
        self.assertEqual(plan['books'][0]['dependency_coverage'], 'unreviewed')

    def test_editions_and_changed_readings_have_distinct_bindings(self):
        rows = [dict(id='b', book_id='one', ingredients=['2 eggs']),
                dict(id='a', book_id='two', ingredients=['1 apple'])]
        sources = {'one': 'a' * 64, 'two': 'b' * 64}
        before = pending_import_review(rows, sources)
        self.assertEqual(before, pending_import_review(list(reversed(rows)), sources))
        self.assertEqual(before['books'][0]['imported_record_ids'], ['b'])
        rows[0]['ingredients'] = ['3 eggs']
        after = pending_import_review(rows, sources)
        self.assertNotEqual(before['books'][0]['imported_records_sha256'],
                            after['books'][0]['imported_records_sha256'])
        self.assertEqual(before['books'][1], after['books'][1])

    def test_bad_identity_never_produces_a_review_plan(self):
        row = dict(id='r', book_id='one')
        for rows, sources in [([row, row], {'one': 'a' * 64}),
                              ([row], {'other': 'b' * 64}),
                              ([row], {'one': 'missing'}), ([], {})]:
            with self.subTest(rows=rows, sources=sources), self.assertRaises(ValueError):
                pending_import_review(rows, sources)

    def test_review_plan_rejects_a_record_from_another_edition(self):
        row = dict(id='r', book_id='one', source={'sha256': 'b' * 64})
        with self.assertRaisesRegex(ValueError, 'source edition differs'):
            pending_import_review([row], {'one': 'a' * 64})
        self.assertEqual(pending_import_review([row], {'one': 'b' * 64})['books'][0]['imported_record_count'], 1)
