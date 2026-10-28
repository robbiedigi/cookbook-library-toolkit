"""Portable contract checks for reviewed PDF serving records."""
from copy import deepcopy
import unittest
from cookbook.pdf_review_assembly import assemble
from cookbook.pdf_runtime import project, qualify_index_links
from tests.test_pdf_review_assembly import fixture

class PdfRuntimeTests(unittest.TestCase):
    def build(self):
        row,ledger=fixture()
        ledger['recipes'][0].update(aliases=[],note=None)
        ledger['questions']=[dict(id='Q1',recipe_key='soup',issue='Wrong weight')]
        ledger['owner_resolutions']=[dict(issue_id='Q1',decision='Use three carrots',changes=[dict(pdf_page=3,cell_index=1,before='2 carrots',after='3 carrots')])]
        data=assemble('edition',[row],ledger)
        source=dict(book_id='test',title='Test',author='Test',file='test.pdf',sha256='edition')
        return data,source
    def test_preserves_source_correction_and_page_types(self):
        data,source=self.build();before=deepcopy(data)
        r=project(data['recipes'],data['contexts'],data['pages'],source)[0]
        self.assertEqual(data,before)
        self.assertIn('2 carrots',r['source_text'])
        self.assertEqual(r['ingredients'],['3 carrots\nchopped'])
        self.assertEqual(r['source']['pdf_page'],3)
        self.assertEqual(r['source']['printed_page'],'9')
        self.assertEqual(r['normalizations'][0]['kind'],'owner_interpretation')
        self.assertFalse(r['review']['verified_complete_recipe'])
        self.assertRegex(r['id'],r'^[a-f0-9]{16}$')
        data['recipes'][0]['title']='Renamed soup'
        self.assertEqual(project(data['recipes'],[],data['pages'],source)[0]['id'],r['id'])
    def test_variant_never_silently_inherits_ingredients(self):
        data,source=self.build();parent=data['recipes'][0]
        variant=deepcopy(parent);variant.update(id='variant',key='variant',kind='variant',parent_id=parent['id'],parent_key=parent['key'],component_ids=[],variant_ids=[],title='Spicy soup')
        variant['sections']['ingredients']=[];parent['variant_ids']=[variant['id']]
        data['recipes'].append(variant)
        base,child=project(data['recipes'],[],data['pages'],source)
        self.assertEqual(child['ingredients'],[])
        self.assertEqual(child['supporting_notes'][0]['relationship'],'base_recipe')
        self.assertEqual(base['supporting_notes'][0]['relationship'],'variation')
        self.assertEqual(child['parent_id'],base['id'])

    def test_prior_owner_interpretation_label_is_preserved(self):
        data,source=self.build()
        line=data['recipes'][0]['sections']['ingredients'][0]
        line['owner_interpretation_issue_id']=line.pop('owner_resolution_id')
        r=project(data['recipes'],[],data['pages'],source)[0]
        self.assertEqual(r['normalizations'][0]['kind'],'owner_interpretation')
    def test_open_review_blocks_projection(self):
        data,source=self.build();data['recipes'][0]['questions']=[dict(id='Q2')]
        with self.assertRaisesRegex(ValueError,'Unresolved'):
            project(data['recipes'],[],data['pages'],source)

    def test_pdf_index_does_not_infer_optional_use_from_large_excerpt(self):
        row=dict(recipe_resolution=dict(recipe_id='r',status='resolved'),ingredient='banana leaf',
            ingredient_validation=dict(status='corroborated',roles=['optional','alternative'],
                evidence=[dict(kind='ingredient',corroborates=True,text='banana leaf\noptional parsley')]))
        bundle=dict(relationships=[row],report={})
        qualify_index_links(bundle,[dict(id='r',review_key='baked-fish-with-greens')])
        self.assertEqual(row['ingredient_validation']['roles'],['wrapper'])
        self.assertFalse(row['ingredient_validation']['evidence'][0]['optional'])
        self.assertEqual(row['runtime_review']['original_validation']['roles'],['optional','alternative'])

    def test_pdf_locator_disagreement_is_held_for_review(self):
        row=dict(recipe_resolution=dict(recipe_id='r',correction={'kind':'locator_disagreement'},evidence=[]),
            ingredient=None,ingredient_validation=dict(status='not_an_ingredient'))
        qualify_index_links(dict(relationships=[row],report={}),[dict(id='r',review_key='sauce')])
        self.assertIsNone(row['recipe_resolution']['recipe_id'])
        self.assertEqual(row['recipe_resolution']['status'],'unresolved')
