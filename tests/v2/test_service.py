import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from library_v2.store import compile_database, json_text, clean
from library_v2.service import SnapshotService
from library_v2.contracts import revision, validate_output
from test_store import fixtures, reconstruct, assert_source_equivalence

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'library.sqlite'
        books,recipes,knowledge=fixtures()
        for k in knowledge: k['related_recipe_ids']=[];k['revision']=revision(k)
        self.books,self.recipes,self.knowledge=books,recipes,knowledge
        chunks=[dict(chunk_id='c'+str(i),entity='knowledge',item_id=k['id'],ordinal=0,title=k['title'],text='mushroom browning technique',text_sha256=hashlib.sha256(b'mushroom browning technique').hexdigest(),source_spans=[]) for i,k in enumerate(knowledge)]
        relations=[]
        def edge(source,target,disposition,number,group=None):
            return dict(schema_version='library-v2-draft-1',id='edge'+str(number),**{'from':dict(entity='recipe',id=source['id'])},target=dict(entity='recipe',id=target['id']),kind='component',disposition=disposition,application='Use separate batch',choice_group=group,evidence_spans=source['method_blocks'][0]['source_spans'],review_ref='accepted:fixture',review_status='accepted',bindings=[dict(node=dict(entity='recipe',id=x['id']),revision=x['revision']) for x in (source,target)],assembly_sha256=None)
        relations.append(edge(recipes[0],recipes[1],'required',1))
        relations.append(edge(recipes[0],recipes[2],'choice',2,'choose'))
        relations.append(edge(recipes[0],recipes[3],'choice',3,'choose'))
        compile_database(self.path,books=books,recipes=recipes,knowledge=knowledge,relationships=relations,chunks=chunks)
        self.service=SnapshotService(self.path,'x'*32)

    def tearDown(self):
        self.service.close();self.tmp.cleanup()

    def checked(self,response):
        validate_output(response);return response

    def test_search_pagination_and_authenticated_effective_cursors(self):
        all_ids=[];cursor=None
        while True:
            r=self.checked(self.service.search_recipes(limit=1,cursor=cursor))
            all_ids += [x['id'] for x in r['items']]
            cursor=r['next_cursor']
            if not cursor:break
        self.assertEqual(sorted(all_ids),all_ids);self.assertEqual(len(all_ids),4)
        r=self.service.search_recipes(filters={'books':[self.books[0]['title']]},limit=1)
        r2=self.checked(self.service.search_recipes(filters={'books':[self.books[0]['id']]},limit=1,cursor=r['next_cursor']))
        self.assertFalse(r2['errors'])
        bad=self.checked(self.service.search_recipes(query='different',limit=1,cursor=r['next_cursor']))
        self.assertEqual(bad['errors'][0]['code'],'CURSOR_INPUT_MISMATCH')
        bad=self.checked(self.service.search_recipes(limit=1,cursor=r['next_cursor']+'x'))
        self.assertEqual(bad['errors'][0]['code'],'INVALID_CURSOR')

    def test_mixed_roots_dedupe_required_choices(self):
        root=self.recipes[0]['id']; component=self.recipes[1]['id']
        r=self.checked(self.service.get_recipes([root,'bad',root,component]))
        self.assertEqual([d['node']['id'] for d in r['items']],[root,component])
        self.assertEqual(len(r['relationships']),3)
        self.assertEqual(r['roots'][1]['error']['code'],'NOT_FOUND')
        self.assertTrue(r['complete'])
        assert_source_equivalence(r['items'][0]['content'],clean(self.recipes[0]))

    def test_knowledge_search_deduped(self):
        r=self.checked(self.service.search_knowledge('mushroom',limit=1))
        self.assertEqual(len(r['items']),1);self.assertIsNone(r['total'])
        r2=self.checked(self.service.search_knowledge('mushroom',limit=1,cursor=r['next_cursor']))
        self.assertNotEqual(r['items'][0]['id'],r2['items'][0]['id'])
        p=self.checked(self.service.get_passages([r['items'][0]['id']]))
        self.assertTrue(p['items'][0]['complete'])

    def test_knowledge_lookahead_after_byte_budget_dedupes_chunks_and_finishes(self):
        records, chunks = [], []
        for index in range(32):
            item = copy.deepcopy(self.knowledge[0])
            item['id'] = f'budget-knowledge:{index:02}'
            item['title'] = 'mushroom ' + 'descriptive source heading ' * 25
            item['revision'] = revision(item)
            records.append(item)
            for ordinal in range(2):
                text = 'mushroom browning technique ' * 30
                chunks.append(dict(chunk_id=f'budget-chunk:{index:02}:{ordinal}',
                    entity='knowledge', item_id=item['id'], ordinal=ordinal,
                    title=item['title'], text=text,
                    text_sha256=hashlib.sha256(text.encode()).hexdigest(), source_spans=[]))
        path = Path(self.tmp.name) / 'knowledge-budget.sqlite'
        compile_database(path, books=self.books, recipes=[], knowledge=records, chunks=chunks)
        service = SnapshotService(path, 'x' * 32, max_response_bytes=16384)
        try:
            seen, cursor, pages = [], None, 0
            while True:
                page = self.checked(service.search_knowledge('mushroom', limit=50, cursor=cursor))
                self.assertIsNone(page['total'])
                self.assertFalse(page['errors'])
                self.assertLessEqual(len(json_text(page).encode()), 16384)
                seen.extend(item['id'] for item in page['items'])
                pages += 1
                cursor = page['next_cursor']
                self.assertLess(pages, 32)
                if not cursor:
                    self.assertTrue(page['complete'])
                    break
                self.assertFalse(page['complete'])
                self.assertLess(len(page['items']), 50)
            self.assertGreater(pages, 1)
            self.assertEqual(seen, sorted(item['id'] for item in records))
            self.assertEqual(len(seen), len(set(seen)))
            empty = self.checked(service.search_knowledge('nonmatchingwords', limit=50))
            self.assertTrue(empty['complete'])
            self.assertEqual(empty['items'], [])
        finally:
            service.close()

    def test_recipe_discovery_uses_formula_not_attached_method_noise(self):
        recipes=copy.deepcopy(self.recipes)
        for recipe,title in zip(recipes,['Cardamom custard','Vanilla custard','Apple compote','Plain loaf']):
            recipe['title']=title
            recipe['aliases']=[]
            recipe['headnotes']=[]
            for group in recipe['ingredient_groups']:
                for block in group['blocks']:
                    block['text']='1 cup water'
                    block.pop('ingredient_occurrences',None)
            recipe['method_blocks'][0]['text']='Combine the ingredients.'
        recipes[1]['ingredient_groups'][0]['blocks'][0]['text']='1 teaspoon cardamom'
        headnote=copy.deepcopy(recipes[2]['method_blocks'][0])
        headnote.update(id='headnote:cardamom',role='headnote',text='Serve with cardamom.')
        recipes[2]['headnotes']=[headnote]
        recipes[3]['method_blocks'][0]['text']='Cardamom is discussed in this shared chapter. '*100
        for recipe in recipes:
            recipe['revision']=revision(recipe)
        path=Path(self.tmp.name)/'target-search.sqlite'
        compile_database(path,books=self.books,recipes=recipes,knowledge=[],chunks=[])
        service=SnapshotService(path,'x'*32)
        try:
            response=self.checked(service.search_recipes('find me a cardamom recipe please'))
            self.assertEqual([card['id'] for card in response['items']], [r['id'] for r in recipes[:3]])
            card=response['items'][0]
            self.assertIn('sources',card)
            self.assertIn('qualification_codes',card)
            self.assertNotIn('citations',card)
            self.assertNotIn('uncertainties',card)
            self.assertTrue(all('id' not in source and 'epub_href' not in source for source in card['sources']))
        finally:
            service.close()

    def test_explicit_unsupported_and_book_errors(self):
        self.assertEqual(self.checked(self.service.search_recipes(preferences=['light']))['errors'][0]['code'],'INVALID_ARGUMENT')
        self.assertEqual(self.checked(self.service.search_recipes(filters={'books':['absent']}))['errors'][0]['code'],'UNKNOWN_BOOK')
        self.assertEqual(self.checked(self.service.search_recipes(limit=0))['errors'][0]['code'],'INVALID_ARGUMENT')

    def test_no_dependency_expansion_keeps_relationships_and_root_receipts(self):
        root=self.recipes[0]['id']
        response=self.checked(self.service.get_recipes([root,root],include_dependencies=False))
        self.assertEqual([r['id'] for r in response['roots']],[root,root])
        self.assertEqual(len(response['items']),1)
        self.assertEqual(len(response['relationships']),3)
        self.assertEqual(sum(r['disposition']=='choice' for r in response['relationships']),2)

    def test_large_delivery_reassembly_and_release_bound_cursor(self):
        self.service.close()
        from library_v2.store import delivery_parts, digest
        r=copy.deepcopy(self.recipes[0])
        r['method_blocks'][0]['text']='½ teaspoon café\n'*50000
        r['revision']=revision(r)
        path=Path(self.tmp.name)/'large.sqlite'
        compile_database(path,books=self.books,recipes=[r],knowledge=[],chunks=[])
        service=SnapshotService(path,'x'*32)
        cursor=None;parts=[];first_cursor=None
        while True:
            response=self.checked(service.get_recipes([r['id']],cursor=cursor))
            self.assertLessEqual(len(json_text(response).encode()),131072)
            parts.extend(response['items'])
            cursor=response['next_cursor']
            if first_cursor is None:first_cursor=cursor
            if not cursor:break
        returned=reconstruct(parts)
        assert_source_equivalence(returned,clean(r))
        self.assertEqual(sum('header' in part for part in parts),1)
        self.assertTrue(all('content_sha256' not in part for part in parts))
        self.service=SnapshotService(self.path,'x'*32)
        changed=self.checked(self.service.get_recipes([r['id']],cursor=first_cursor))
        self.assertEqual(changed['errors'][0]['code'],'SNAPSHOT_UNAVAILABLE')
        service.close()

    def test_shared_service_immutable_thread_readers(self):
        from concurrent.futures import ThreadPoolExecutor
        def read(_):
            response=self.service.search_recipes(filters={'include_ingredients':['apple']},limit=2)
            return json_text(response),id(self.service.db)
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses=list(pool.map(read,range(100)))
        self.assertEqual(len({r[0] for r in responses}),1)
        self.assertGreater(len({r[1] for r in responses}),1)

    def test_cycles_and_stale_reviews_are_explicit(self):
        self.service.close()
        records=self.recipes
        relationships=[]
        for index,(source,target,status) in enumerate([(records[0],records[1],'accepted'),(records[1],records[0],'accepted'),(records[0],records[2],'stale')]):
            relationships.append(dict(schema_version='library-v2-draft-1',id='cycle'+str(index),**{'from':dict(entity='recipe',id=source['id'])},target=dict(entity='recipe',id=target['id']),kind='component',disposition='required',application='Read component',choice_group=None,evidence_spans=source['method_blocks'][0]['source_spans'],review_ref='accepted:fixture',review_status=status,bindings=[dict(node=dict(entity='recipe',id=x['id']),revision=x['revision']) for x in (source,target)],assembly_sha256=None))
        path=Path(self.tmp.name)/'cycles.sqlite'
        compile_database(path,books=self.books,recipes=records,knowledge=[],relationships=relationships,chunks=[])
        self.service=SnapshotService(path,'x'*32)
        response=self.checked(self.service.get_recipes([records[0]['id'],records[1]['id']]))
        codes={e['code'] for e in response['errors']}
        self.assertIn('DEPENDENCY_CYCLE',codes);self.assertIn('STALE_REVIEW',codes)
        self.assertEqual(len(response['items']),2)
        self.assertTrue(response['complete'])
        from unittest.mock import patch
        with patch('library_v2.service.MAX_NODES',1):
            bounded=self.checked(self.service.get_recipes([records[0]['id']]))
        self.assertIn('DEPENDENCY_LIMIT',{e['code'] for e in bounded['errors']})
        self.assertEqual(len(bounded['items']),1)

    def test_required_ingredient_presence_keeps_unknowns_unknown(self):
        self.service.close()
        records=[]
        for item_id,optional,variant in [('required',False,None),('unknown',None,None),('optional',True,None),('variant',False,'alternative')]:
            record=copy.deepcopy(self.recipes[0]);record['id']=item_id
            for group in record['ingredient_groups']:
                for block in group['blocks']:
                    block['text']='1 fresh apple'
                    block['ingredient']=dict(identity='apple',role='primary',optional=optional,variant_scope=variant)
            record['revision']=revision(record);records.append(record)
        path=Path(self.tmp.name)/'presence.sqlite'
        compile_database(path,books=self.books,recipes=records,knowledge=[],chunks=[])
        self.service=SnapshotService(path,'x'*32)
        required=self.checked(self.service.search_recipes(filters={'include_ingredients':['apple']}))
        self.assertEqual([r['id'] for r in required['items']],['required'])
        any_presence=self.checked(self.service.search_recipes(filters={'include_ingredients':[{'name':'apple','presence':'any_variant_or_optional'}]}))
        self.assertEqual(len(any_presence['items']),4)
        excluded=self.checked(self.service.search_recipes(filters={'exclude_ingredients':['apple']}))
        self.assertEqual(excluded['items'],[])

    def test_source_block_masks_cross_wrap_without_borrowing_optional_identity(self):
        self.service.close();records=[]
        cases=[('derivative','1 cup apple\ncider vinegar'),('optional-fruit','1 cup apple\ncider vinegar\n1 apple (optional)'),('fruit','1 apple\n2 tablespoons cider vinegar'),('fish-derivative','1 tablespoon fish\nsauce')]
        for item_id,text in cases:
            record=copy.deepcopy(self.recipes[0]);record['id']=item_id
            record['ingredient_groups']=record['ingredient_groups'][:1];group=record['ingredient_groups'][0];group['blocks']=group['blocks'][:1]
            block=group['blocks'][0];block['text']=text;occurrences=[];offset=0
            for line in text.splitlines():
                occurrences.append(dict(start=offset,end=offset+len(line),text=line,optional=True if '(optional)' in line else False if line[0].isdigit() else None,variant_scope=None,presence_basis='source_fixture'))
                offset+=len(line)+1
            block['ingredient_occurrences']=occurrences;record['revision']=revision(record);records.append(record)
        path=Path(self.tmp.name)/'source-scoped.sqlite'
        compile_database(path,books=self.books,recipes=records,knowledge=[],chunks=[])
        self.service=SnapshotService(path,'x'*32)
        def ids(name,presence):
            result=self.checked(self.service.search_recipes(filters={'include_ingredients':[dict(name=name,presence=presence)]}))
            return {item['id'] for item in result['items']}
        self.assertEqual(ids('apple','any_variant_or_optional'),{'fruit','optional-fruit'})
        self.assertEqual(ids('apple','required_only'),{'fruit'})
        self.assertEqual(ids('apple cider vinegar','any_variant_or_optional'),{'derivative','optional-fruit'})
        self.assertEqual(ids('apple cider vinegar','required_only'),set())
        self.assertEqual(ids('fish','any_variant_or_optional'),set())
        self.assertEqual(ids('fish sauce','any_variant_or_optional'),{'fish-derivative'})
        self.assertEqual(self.service.db.execute('SELECT count(*) FROM ingredient_sources').fetchone()[0],4)
        self.assertEqual(self.service.db.execute('SELECT count(*) FROM ingredient_fts').fetchone()[0],4)

    def test_coherent_read_targets_and_exact_broader_section_suppression(self):
        self.service.close()
        section=copy.deepcopy(self.knowledge[0])
        section.pop('menu',None);section['kind']='passage';section['revision']=revision(section)
        context=copy.deepcopy(section);context['id']='context:complete-block'
        context['revision']=revision(context)
        passage=copy.deepcopy(self.knowledge[1]);passage['id']='passage:partial'
        passage['revision']=revision(passage)
        targets=[dict(id=passage['id'],targets=[dict(entity='knowledge',id=context['id'])],source_block_ids=[b['id'] for b in context['blocks']],parent_ids=[section['id']],policy='complete-source-blocks-v1')]
        path=Path(self.tmp.name)/'contexts.sqlite'
        compile_database(path,books=self.books,recipes=self.recipes,knowledge=[section,context,passage],chunks=[],read_targets=targets)
        self.service=SnapshotService(path,'x'*32)
        coherent=self.checked(self.service.get_passages([passage['id']]))
        self.assertEqual(coherent['items'][0]['node']['id'],context['id'])
        self.assertEqual(coherent['roots'][0]['delivered_as'],[dict(entity='knowledge',id=context['id'])])
        combined=self.checked(self.service.get_passages([section['id'],passage['id']]))
        self.assertEqual([i['node']['id'] for i in combined['items']],[section['id']])
        self.assertEqual(combined['roots'][1]['delivered_as'],[dict(entity='knowledge',id=section['id'])])

    def test_partial_overlap_keeps_unique_blocks_and_structured_menu(self):
        self.service.close()
        source=copy.deepcopy(self.knowledge[0]);source.pop('menu',None);source['kind']='passage'
        original=source['blocks'][0]
        blocks=[]
        for index in range(3):
            block=copy.deepcopy(original);block['id']='overlap-block-'+str(index);block['text']='Complete source paragraph '+str(index);blocks.append(block)
        first=copy.deepcopy(source);first.update(id='overlap:first',blocks=blocks[:2]);first['revision']=revision(first)
        second=copy.deepcopy(source);second.update(id='overlap:second',blocks=blocks[1:]);second['revision']=revision(second)
        menu=copy.deepcopy(self.knowledge[0]);menu['id']='overlap:menu';menu['blocks']=blocks[:2]
        for dish in menu['menu']['dishes']:dish['source_block_ids']=[blocks[0]['id']]
        menu['menu']['preparation_note_block_ids']=[];menu['revision']=revision(menu)
        path=Path(self.tmp.name)/'overlap.sqlite'
        compile_database(path,books=self.books,recipes=self.recipes,knowledge=[first,second,menu],chunks=[])
        self.service=SnapshotService(path,'x'*32)
        partial=self.checked(self.service.get_passages([first['id'],second['id']]))
        self.assertEqual(len(partial['items']),2)
        self.assertEqual({b if isinstance(b,str) else b['text'] for item in partial['items'] for b in item['content']['blocks']},{b['text'] for b in blocks})
        structured=self.checked(self.service.get_passages([first['id'],menu['id']]))
        self.assertIn(menu['id'],[item['node']['id'] for item in structured['items']])
        assert_source_equivalence(next(item['content'] for item in structured['items'] if item['node']['id']==menu['id']),clean(menu))





class IngredientCandidateParityTests(unittest.TestCase):
    def test_all_accepted_terms_aliases_families_and_adversarial_lines(self):
        from library_v2.store import INGREDIENTS,INGREDIENT_ALIASES,INGREDIENT_GROUPS,ingredient_candidate_query
        lines=['1 apple','apple cider vinegar','apple cider','apple butter','applesauce','berries','berry','anchovies',
            'fresh anchovy','beef or chicken stock','chicken or beef broth','oxtail','pork oxtail','beef tomato','beefsteak tomato',
            'oyster mushrooms','mixed mushrooms, such as cremini (chestnut) and oyster','oyster sauce','vegetarian oyster sauce',
            'vegan fish sauce','ﬁsh sauce','ﬁsh fillet','turkey bacon','bacon','duck bacon','pork belly','lamb’s lettuce',
            'aubergines','courgettes','garbanzo beans','prawns','rocket','beetroot','capsicum','finocchio','coriander leaves',
            'arctic char','artic char','cocoa butter','cacao nibs','chocolate clams','chocolate','café beans','味噌',
            'fennel seeds','fennel bulbs','chicken of the woods','chicken fried steak','chicken stock','chicken thighs',
            'king oyster mushroom','fish mint','scallop squash','salmon','shellfish','kina','john dory','rib eye steak',
            'oyster crackers','beef fat','vegetable stock','berry jelly','orange peel','potatoes','peaches','radishes']
        books,fixtures_recipes,_=fixtures();recipes=[]
        for index,line in enumerate(lines):
            record=copy.deepcopy(fixtures_recipes[0]);record['id']='candidate:'+str(index)
            record['ingredient_groups']=record['ingredient_groups'][:1];record['ingredient_groups'][0]['blocks']=record['ingredient_groups'][0]['blocks'][:1]
            block=record['ingredient_groups'][0]['blocks'][0];block['text']=line
            block['ingredient']=dict(identity=None,role='unknown',optional=index%7==0,variant_scope='choice' if index%11==0 else None)
            record['revision']=revision(record);recipes.append(record)
        terms=sorted(set(INGREDIENTS)|set(INGREDIENT_ALIASES)|set(INGREDIENT_GROUPS)|{'cafe','味噌','/','berry','anchovy','coriander leaves'})
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'candidate-parity.sqlite';compile_database(path,books=books,recipes=recipes,knowledge=[],chunks=[])
            fast=SnapshotService(path,'x'*32);slow=SnapshotService(path,'x'*32,ingredient_prefilter=False)
            def collect(service,filters):
                ids=[];cursor=None
                while True:
                    result=service.search_recipes(filters=filters,limit=50,cursor=cursor)
                    self.assertFalse(result['errors'],result['errors'])
                    ids.extend(item['id'] for item in result['items']);cursor=result['next_cursor']
                    if not cursor:return ids
            for term in terms:
                for field in ('include_ingredients','exclude_ingredients'):
                    for presence in ('required_only','any_variant_or_optional'):
                        with self.subTest(term=term,field=field,presence=presence):
                            filters={field:[dict(name=term,presence=presence)]}
                            self.assertEqual(collect(fast,filters),collect(slow,filters))
            for family in INGREDIENT_GROUPS:
                filters={'include_ingredients':[dict(name=family[6:],match='family',presence='any_variant_or_optional')]}
                self.assertEqual(collect(fast,filters),collect(slow,filters))
            self.assertIsNone(ingredient_candidate_query('味噌'));self.assertIsNone(ingredient_candidate_query('/'))
            fast.close();slow.close()


class PassageSemanticDedupeTests(unittest.TestCase):
    def test_equal_text_preserves_distinct_warnings_coverage_and_block_scope(self):
        """Equal source text is insufficient to discard different cooking evidence."""
        for difference in ('qualification_coverage', 'citation', 'applicability', 'role'):
            with self.subTest(difference=difference):
                books, _, knowledge = fixtures()
                broad = copy.deepcopy(knowledge[0])
                broad.update(id='semantic:broad', kind='passage', related_recipe_ids=[])
                broad.pop('menu', None)
                narrow = copy.deepcopy(broad)
                narrow.update(id='semantic:narrow', title='Qualified subsection')
                if difference == 'qualification_coverage':
                    narrow['uncertainties'].append(dict(
                        code='MISSING_DURATION', message='The resting duration is unspecified.',
                        affects=['method'], action='source_gap', citation_ids=[narrow['citations'][0]['id']]))
                    narrow['dependency_coverage'] = dict(status='unknown',
                        scope='Only the first preparation step was retained.',
                        limitations=['The second preparation is not reviewed.'])
                elif difference == 'citation':
                    citation = copy.deepcopy(narrow['citations'][0])
                    citation.update(id='semantic:other-page', printed_labels=['99'])
                    narrow['citations'].append(citation)
                    narrow['blocks'][0]['source_spans'][0]['citation_id'] = citation['id']
                elif difference == 'applicability':
                    narrow['blocks'][0]['applicability'] = 'Only for the optional sauce.'
                else:
                    narrow['blocks'][0]['role'] = 'heading'
                for item in (broad, narrow):
                    item['revision'] = revision(item)
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'semantic.sqlite'
                    compile_database(path, books=books, recipes=[], knowledge=[broad, narrow], chunks=[])
                    service = SnapshotService(path, 'semantic-dedupe-test-secret-000000')
                    try:
                        for ordered in ([broad, narrow], [narrow, broad]):
                            response = service.get_passages([item['id'] for item in ordered])
                            validate_output(response)
                            self.assertTrue(response['complete'])
                            self.assertFalse(response['errors'])
                            self.assertEqual([part['node']['id'] for part in response['items']],
                                             [item['id'] for item in ordered])
                            for source, root, part in zip(ordered, response['roots'], response['items']):
                                self.assertEqual(root['delivered_as'],
                                                 [dict(entity='knowledge', id=source['id'])])
                                assert_source_equivalence(part['content'], clean(source))
                    finally:
                        service.close()
