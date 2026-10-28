"""Portable synthetic cases: no cookbook text or private corpus required."""
from copy import deepcopy
import unittest
from cookbook.pdf_review_assembly import assemble, recipe_id, selected


def fixture():
    row = dict(pdf_page=3, raw_page={'cells': ['original']}, review_lines=[
        dict(pdf_page=3, cell_index=0, text='Garden soup'),
        dict(pdf_page=3, cell_index=1, text='2 carrots'),
        dict(pdf_page=3, cell_index=None, insert_after_original_cell=1, text='chopped'),
        dict(pdf_page=3, cell_index=2, text='Simmer until tender.')])
    recipe = dict(key='soup', title='Garden soup', kind='recipe', parent_key=None,
                  parts=[dict(pdf_page=3, title=[[0,0]], ingredients=[[1,1]], method=[[2,2]])])
    ledger = dict(pages={'3': {'printed_page':'9'}}, recipes=[recipe], corrections=[], questions=[])
    return row, ledger


class ReviewAssemblyTests(unittest.TestCase):
    def test_owner_resolution_preserves_transcription_and_resolves_question(self):
        row, ledger = fixture()
        ledger['questions'] = [dict(id='Q1', recipe_key='soup', issue='Amount conflict')]
        ledger['owner_resolutions'] = [dict(issue_id='Q1', decision='Use three carrots', changes=[
            dict(pdf_page=3, cell_index=1, before='2 carrots', after='3 carrots')])]
        result = assemble('source', [row], ledger)
        page, recipe = result['pages'][0], result['recipes'][0]
        assert page['recipe_reviewed_lines'][1]['text'] == '2 carrots'
        assert page['recipe_owner_interpreted_lines'][1]['text'] == '3 carrots'
        assert recipe['sections']['ingredients'][0]['owner_resolution_id'] == 'Q1'
        assert not recipe['questions']
        assert len(recipe['resolved_source_questions']) == 1
        assert not recipe['verified_complete_recipe']
        ledger['owner_resolutions'][0]['changes'][0]['before'] = 'stale'
        with self.assertRaisesRegex(ValueError, 'Stale owner'):
            assemble('source', [row], ledger)

    def test_owner_retention_resolves_only_named_question(self):
        row, ledger = fixture()
        ledger['questions'] = [dict(id=i, recipe_key='soup', issue='Printed conflict') for i in ['Q1','Q2']]
        ledger['owner_resolutions'] = [dict(issue_id='Q1', decision='Retain printed yield', changes=[])]
        result = assemble('source', [row], ledger)
        assert [q['id'] for q in result['recipes'][0]['questions']] == ['Q2']
        assert 'recipe_owner_interpreted_lines' not in result['pages'][0]

    def test_insertions_and_review_order_are_retained(self):
        row, ledger = fixture()
        assert [l['text'] for l in selected(row['review_lines'], [[1,1]])] == ['2 carrots','chopped']
        result = assemble('source', [row], ledger)
        assert result['recipes'][0]['source_locations'] == [{'pdf_page':3,'printed_page':'9'}]
        assert result['pages'][0]['raw_page'] == row['raw_page']


    def test_correction_preserves_original_and_rejects_stale_text(self):
        row, ledger = fixture()
        ledger['corrections'] = [dict(pdf_page=3, cell_index=1, before='2 carrots', after='3 carrots')]
        before = deepcopy(row)
        result = assemble('source', [row], ledger)
        assert row == before
        assert result['pages'][0]['review_lines'] == before['review_lines']
        assert result['recipes'][0]['sections']['ingredients'][0]['text'] == '3 carrots'
        ledger['corrections'][0]['before'] = 'wrong source'
        with self.assertRaisesRegex(ValueError, 'Stale'):
            assemble('source', [row], ledger)


    def test_owner_layer_takes_precedence_and_is_retained(self):
        row, ledger = fixture()
        row['owner_interpreted_lines'] = deepcopy(row['review_lines'])
        row['owner_interpreted_lines'][1]['text'] = 'owner-approved amount'
        result = assemble('source', [row], ledger)
        line = result['recipes'][0]['sections']['ingredients'][0]
        assert line['text'] == 'owner-approved amount'
        assert line['selected_layer'] == 'owner_interpreted_lines'


    def test_id_stays_stable_on_title_and_order_changes(self):
        _, ledger = fixture()
        recipe = ledger['recipes'][0]
        old_id = recipe_id('source', recipe)
        recipe['title'] = 'Corrected title'
        recipe['key'] = 'renamed'
        assert recipe_id('source', recipe) == old_id
        assert recipe_id('different edition', recipe) != old_id


    def test_unreviewed_page_and_accidental_overlap_fail(self):
        row, ledger = fixture()
        ledger['pages'] = {}
        with self.assertRaisesRegex(ValueError, 'Unreviewed'):
            assemble('source', [row], ledger)
        ledger['pages'] = {'3': {'printed_page':'9'}}
        ledger['recipes'].append(dict(key='other', title='Other', kind='recipe', parent_key=None,
            parts=[dict(pdf_page=3, ingredients=[[1,1]], method=[[2,2]])]))
        with self.assertRaisesRegex(ValueError, 'overlap'):
            assemble('source', [row], ledger)


    def test_source_uncertainty_never_becomes_verified_recipe(self):
        row, ledger = fixture()
        ledger['questions'] = [dict(recipe_key='soup', issue='Printed yield conflict')]
        r = assemble('source', [row], ledger)['recipes'][0]
        assert r['status'] == 'source_reviewed_with_uncertainty'
        assert not r['verified_complete_recipe']
        assert not r['personally_tested']
