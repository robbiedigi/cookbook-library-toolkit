import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from library_v2.contracts import revision, validate_output
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from evaluate_v2_delivery import reconstruct,assert_source_equivalence
from library_v2.store import compile_database, Snapshot, CompileError, ingredient_matches, clean, digest, delivery_parts

ROOT=Path(__file__).resolve().parents[2]
EXAMPLES=json.loads((ROOT/'evals/library-v2/foundations/contracts/examples.json').read_text())['canonical']


def fixtures():
    records=copy.deepcopy(EXAMPLES)
    books=[r for r in records if r.get('entity')=='book']
    recipes=[r for r in records if r.get('entity')=='recipe']
    knowledge=[r for r in records if r.get('entity')=='knowledge']
    for r in books+recipes+knowledge:
        r['revision']=revision(r)
    return books,recipes,knowledge


class StoreTests(unittest.TestCase):
    def test_multiline_occurrences_preserve_text_and_reject_stale_offsets(self):
        books,recipes,_=fixtures()
        recipe=recipes[0]
        block=recipe['ingredient_groups'][0]['blocks'][0]
        block['text']='1 cup rice\n½ lemon (optional)'
        block['ingredient_occurrences']=[
            dict(start=0,end=10,text='1 cup rice',optional=False,variant_scope=None,presence_basis='source_quantity_line'),
            dict(start=11,end=len(block['text']),text='½ lemon (optional)',optional=True,variant_scope=None,presence_basis='explicit_optional')]
        recipe['revision']=revision(recipe)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'good.sqlite'
            compile_database(path,books=books,recipes=[recipe],knowledge=[],chunks=[])
            snap=Snapshot(path)
            rows=snap.db.execute('SELECT optional,source_json FROM ingredient_occurrences WHERE recipe_id=? ORDER BY rowid',(recipe['id'],)).fetchall()
            texts={json.loads(r['source_json'])['text']:r['optional'] for r in rows}
            self.assertEqual(texts['1 cup rice'],0)
            self.assertEqual(texts['½ lemon (optional)'],1)
            cleanblock=snap.get('recipe',recipe['id'])['ingredient_groups'][0]['blocks'][0]
            self.assertEqual(cleanblock['text'],block['text'])
            self.assertNotIn('ingredient_occurrences',cleanblock)
            snap.close()
            block['ingredient_occurrences'][1]['start']=10
            recipe['revision']=revision(recipe)
            with self.assertRaisesRegex(CompileError,'Invalid ingredient occurrence source range'):
                compile_database(Path(tmp)/'bad.sqlite',books=books,recipes=[recipe],knowledge=[],chunks=[])

    def test_compile_frozen_and_identity(self):
        books,recipes,knowledge=fixtures()
        # Synthetic example links may intentionally target IDs outside the slice.
        for k in knowledge: k['related_recipe_ids']=[]; k['revision']=revision(k)
        chunks=[dict(chunk_id='c1',entity='knowledge',item_id=knowledge[0]['id'],ordinal=0,title='Browning',text='brown mushrooms',text_sha256=__import__('hashlib').sha256(b'brown mushrooms').hexdigest(),source_spans=[])]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'library.sqlite'
            result=compile_database(path,books=books,recipes=recipes,knowledge=knowledge,chunks=chunks)
            snap=Snapshot(path)
            self.assertEqual(result['snapshot'],snap.id)
            self.assertEqual(snap.db.execute('PRAGMA query_only').fetchone()[0],1)
            self.assertEqual(snap.get('recipe',recipes[0]['id']),clean(recipes[0]))
            with self.assertRaises(sqlite3.OperationalError): snap.db.execute('DELETE FROM recipes')
            snap.close()
            with self.assertRaises(FileExistsError): compile_database(path,books=[],recipes=[],knowledge=[])
        self.assertFalse(ingredient_matches('apple',['apple cider vinegar']))
        self.assertTrue(ingredient_matches('beef',['oxtail']))
        self.assertFalse(ingredient_matches('oyster',['oyster mushrooms']))

    def test_bad_revision_and_duplicate_are_atomic(self):
        books,recipes,_=fixtures()
        for candidate in [dict(recipes[0],title='changed'),recipes[0]]:
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'library.sqlite'
                inputs=[candidate] if candidate['title']=='changed' else [candidate,candidate]
                with self.assertRaises((CompileError,sqlite3.IntegrityError)):
                    compile_database(path,books=books,recipes=inputs,knowledge=[])
                self.assertFalse(path.exists());self.assertFalse(path.with_name(path.name+'.building').exists())

    def test_text_parts_lossless_and_table_rows(self):
        _,recipes,_=fixtures()
        r=recipes[0]
        r['method_blocks'][0]['text']='½\n' * 30000
        r['revision']=revision(r)
        parts=list(delivery_parts(r))
        returned=reconstruct(parts)
        assert_source_equivalence(returned,clean(r))
        self.assertEqual(sum('header' in p for p in parts),1)
        self.assertTrue(all('content_sha256' not in p and 'revision' not in p for p in parts))
        self.assertTrue(parts[-1]['complete'])
        table=copy.deepcopy(r['method_blocks'][0]);table.update(kind='table',text='',rows=[['header']]+[[str(i)*300] for i in range(1000)],header_rows=[0])
        r['method_blocks']=[table];r['revision']=revision(r)
        parts=list(delivery_parts(r))
        returned=reconstruct(parts)
        self.assertEqual(returned['method'][0]['rows'],table['rows'])
        self.assertEqual(parts[-1]['segments'][-1]['row_range']['end'],1001)

    def test_oversized_table_cell_is_explicit_compiler_hold(self):
        _,recipes,_=fixtures()
        r=recipes[0]
        block=r['method_blocks'][0]
        block.update(kind='table',text='',rows=[['header'],['quantity '*30000]],header_rows=[0])
        r['revision']=revision(r)
        with self.assertRaisesRegex(CompileError,'Oversized table row or header'):
            list(delivery_parts(r))

    def test_stale_accepted_binding_and_bad_choice_rejected(self):
        books,recipes,_=fixtures()
        source,target=recipes[:2]
        relation=dict(schema_version='library-v2-draft-1',id='binding-test',**{'from':dict(entity='recipe',id=source['id'])},target=dict(entity='recipe',id=target['id']),kind='component',disposition='required',application='Read component',choice_group=None,evidence_spans=source['method_blocks'][0]['source_spans'],review_ref='accepted:fixture',review_status='accepted',bindings=[dict(node=dict(entity='recipe',id=source['id']),revision='0'*64),dict(node=dict(entity='recipe',id=target['id']),revision=target['revision'])],assembly_sha256=None)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(CompileError,'Stale accepted relationship binding'):
                compile_database(Path(tmp)/'bad.sqlite',books=books,recipes=recipes,knowledge=[],relationships=[relation],chunks=[])
        relation['bindings'][0]['revision']=source['revision'];relation.update(disposition='choice',choice_group='only-one')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(CompileError,'at least two distinct'):
                compile_database(Path(tmp)/'choice.sqlite',books=books,recipes=recipes,knowledge=[],relationships=[relation],chunks=[])

    def test_context_projection_keeps_knowledge_and_cooking_notes(self):
        _,recipes,knowledge=fixtures()
        recipe=recipes[0]
        context=copy.deepcopy(recipe['method_blocks'][0]);context.update(id='supporting:context',role='context',text='General neighboring chapter context')
        recipe['notes'].append(context)
        recipe['context_links']=[dict(node=dict(entity='knowledge',id=knowledge[0]['id']),title='Source context',applicability='unreviewed')]
        projection=clean(recipe)
        self.assertFalse(any(b['role']=='context' for b in projection['notes']))
        self.assertEqual(projection['context_links'],recipe['context_links'])
        knowledge[0]['blocks'].append(context)
        self.assertIn('General neighboring chapter context',[b['text'] for b in clean(knowledge[0])['blocks']])

    def test_serving_prunes_audit_copies_and_keeps_foreign_book_guard(self):
        books,recipes,knowledge=fixtures()
        for item in knowledge:item['related_recipe_ids']=[];item['revision']=revision(item)
        mapping=dict(old_namespace='passage',old_id='old:passage',old_revision='1'*64,status='exact',targets=[dict(node=dict(entity='knowledge',id=knowledge[0]['id']),source_spans=knowledge[0]['blocks'][0]['source_spans'])],review_ref=None)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'pruned.sqlite'
            compile_database(path,books=books,recipes=recipes,knowledge=knowledge,chunks=[],mappings=[mapping])
            snap=Snapshot(path)
            names={row[0] for row in snap.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn('citations',names);self.assertNotIn('block_coverage',names)
            self.assertNotIn('source_spans_json',{row['name'] for row in snap.db.execute('PRAGMA table_info(search_chunks)')})
            self.assertNotIn('content_json',{row['name'] for row in snap.db.execute('PRAGMA table_info(read_targets)')})
            self.assertEqual(json.loads(snap.db.execute('SELECT targets_json FROM id_mappings').fetchone()[0]),[dict(node=mapping['targets'][0]['node'])])
            self.assertEqual(snap.db.execute('SELECT count(*) FROM ingredient_fts').fetchone()[0],snap.db.execute('SELECT count(*) FROM ingredient_sources').fetchone()[0])
            snap.close()
            recipe=copy.deepcopy(recipes[0]);recipe['citations'][0]['edition_id']='unknown:source-book';recipe['revision']=revision(recipe)
            with self.assertRaisesRegex(CompileError,'Missing citation edition'):
                compile_database(Path(tmp)/'bad-source-book.sqlite',books=books,recipes=[recipe],knowledge=[],chunks=[])
