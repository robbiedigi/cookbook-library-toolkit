import copy
import unittest
from library_v2.reader import cooking_item, delivery_parts, archive_policy
from library_v2.store import clean, SCHEMA_VERSION
from library_v2.contracts import revision, validate_content, validate_output
from test_store import fixtures, reconstruct, assert_source_equivalence


class ReaderTests(unittest.TestCase):
    def test_plain_cooking_text_and_exact_generic_audit_classification(self):
        _,recipes,_=fixtures();item=recipes[0]
        item['dependency_coverage']=dict(status='unknown',scope=archive_policy()['generic_coverage_scopes'][0],limitations=archive_policy()['generic_coverage_limitations'][:1]+['Only the first method page is available.'])
        generic=archive_policy()['archive_only_qualifications'][0]
        item['uncertainties']=[dict(generic,affects=['recipe'],action='inspect_source',citation_ids=[]),dict(code=generic['code'],message=generic['message']+' Quantity remains unresolved.',affects=['yield'],action='inspect_source',citation_ids=[])]
        for group in item['ingredient_groups']:
            for block in group['blocks']:block['basis']='accepted_correction'
        result=cooking_item(item)
        self.assertTrue(all(isinstance(b,str) for g in result['ingredients'] for b in g['items']))
        self.assertEqual(result['dependency_coverage'],dict(status='unknown',limitations=['Only the first method page is available.']))
        self.assertEqual(len(result['qualifications']),1)
        self.assertIn('Quantity remains unresolved.',result['qualifications'][0]['message'])
        assert_source_equivalence(result,clean(item))

    def test_page_overrides_and_generated_inference_stay_explicit(self):
        _,recipes,_=fixtures();item=recipes[0]
        citation=copy.deepcopy(item['citations'][0]);citation.update(id='other-page',printed_labels=[],pdf_file_pages=[21])
        item['citations'].append(citation)
        method=item['method_blocks'][0];method['source_spans'][0]['citation_id']='other-page';method['basis']='generated_inference'
        result=cooking_item(item);value=result['method'][0]
        self.assertEqual(value['basis'],'generated_inference')
        refs=value.get('source_refs',result.get('source_refs',[0] if len(result['sources'])==1 else []))
        self.assertEqual([result['sources'][i].get('pdf_file_pages') for i in refs],[[21]])
        self.assertTrue(all('printed_pages' not in result['sources'][i] for i in refs))
        assert_source_equivalence(result,clean(item))

    def test_header_once_text_table_reconstruction_and_missing_range_rejection(self):
        _,recipes,_=fixtures();item=recipes[0]
        item['method_blocks'][0]['text']='½ café\n'*3000
        parts=list(delivery_parts(item,part_bytes=4096))
        self.assertEqual(sum('header' in p for p in parts),1)
        self.assertTrue(all('revision' not in p and 'content_sha256' not in p for p in parts))
        assert_source_equivalence(reconstruct(parts),clean(item))
        broken=copy.deepcopy(parts)
        ranges=[s for p in broken[1:] for s in p['segments'] if 'text_range' in s]
        ranges[1]['text_range']['start']+=1
        with self.assertRaisesRegex(AssertionError,'Gap, overlap'):reconstruct(broken)
        table=item['method_blocks'][0];table.update(kind='table',text='Caption',rows=[['HEAD']]+[[str(i)*50] for i in range(200)],header_rows=[0])
        parts=list(delivery_parts(item,part_bytes=4096))
        assert_source_equivalence(reconstruct(parts),clean(item))
        ranges=[s for p in parts[1:] for s in p['segments'] if 'row_range' in s]
        first=next(s for p in parts[1:] for s in p['segments'] if s['section']=='method')
        self.assertEqual(first['value']['header_rows'],[0])
        self.assertTrue(all('header_rows' not in s['value'] for s in ranges))

    def test_oversized_table_caption_survives_with_empty_and_nonempty_rows(self):
        _,recipes,_=fixtures();item=recipes[0]
        block=item['method_blocks'][0]
        caption='Critical table caption ½ café\n'*10000
        for rows,headers in [([],[]),([['HEAD'],['2','cups']],[0])]:
            with self.subTest(rows=len(rows)):
                block.update(kind='table',text=caption,rows=rows,header_rows=headers)
                item['revision']=revision(item);validate_content(item)
                parts=list(delivery_parts(item))
                for part in parts:
                    validate_output(dict(schema_version=SCHEMA_VERSION,snapshot='caption-regression',next_cursor=None,page_index=0,complete=True,errors=[],roots=[],items=[part],relationships=[]))
                retained=copy.deepcopy(parts)
                result=reconstruct(parts)
                self.assertEqual(parts,retained)
                self.assertEqual(reconstruct(parts),result)
                self.assertEqual(parts,retained)
                self.assertTrue(parts[-1]['complete'])
                self.assertEqual(len(result['method']),1)
                self.assertEqual(result['method'][0]['text'],caption)
                self.assertEqual(result['method'][0]['rows'],rows)
                self.assertEqual(result['method'][0]['header_rows'],headers)
                assert_source_equivalence(result,clean(item))
                # Caption and rows have independent source coordinates. A gap
                # in either field must not be hidden by the other's total.
                if rows:
                    broken=copy.deepcopy(parts)
                    row=next(s for p in broken[1:] for s in p['segments'] if 'row_range' in s)
                    row['row_range']['start']=1
                    with self.assertRaisesRegex(AssertionError,'Gap, overlap'):reconstruct(broken)

    def test_independent_proof_rejects_heading_and_generated_yield_loss(self):
        _,recipes,_=fixtures();item=recipes[0]
        item['method_blocks'][0]['role']='heading'
        item['yield']['basis']='generated_inference'
        result=cooking_item(item)
        assert_source_equivalence(result,clean(item))
        for mutation in ('drop_role','change_role','drop_yield_basis','change_yield_basis'):
            broken=copy.deepcopy(result)
            if mutation=='drop_role':broken['method'][0].pop('role')
            elif mutation=='change_role':broken['method'][0]['role']='method'
            elif mutation=='drop_yield_basis':broken.pop('yield_basis')
            else:broken['yield_basis']='source'
            with self.subTest(mutation=mutation),self.assertRaisesRegex(AssertionError,'heading|yield basis'):
                assert_source_equivalence(broken,clean(item))

    def test_unknown_scope_and_qualification_survive_and_menu_links_are_indices(self):
        _,_,knowledge=fixtures();item=knowledge[0]
        result=cooking_item(item)
        assert_source_equivalence(result,clean(item))
        self.assertTrue(all('source_block_ids' not in dish for dish in result['menu']['dishes']))
        self.assertTrue(all('block_indices' in dish for dish in result['menu']['dishes']))
        modified=copy.deepcopy(result);modified['blocks']=modified['blocks'][1:]
        with self.assertRaises(AssertionError):assert_source_equivalence(modified,clean(item))
