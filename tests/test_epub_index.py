import base64
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from cookbook.epub_index import capture, save, verify_capture


class EpubIndexTests(unittest.TestCase):
    def test_cumulative_index_does_not_assign_volume_one_pages_to_volume_two(self):
        from cookbook.index_relationships import locators
        with tempfile.TemporaryDirectory() as folder:
            path=self.make_book(Path(folder),'<p class="primary">Almonds, I: 21, 582</p><p class="primary">Sauce, I: 92; <a href="chapter.xhtml#page_14">14</a></p>', '<p id="page_14">Sauce</p>')
            result=capture(path,'mastering-the-art-of-french-cooking-volume-2-julia-child','Volume 2','Authors')
            self.assertEqual(result['entries'][0]['label'],'Almonds')
            self.assertEqual(result['entries'][0]['printed_locators'][0]['volume'],1)
            self.assertEqual(locators(result['entries'][0]),[])
            self.assertEqual([x['printed_page'] for x in locators(result['entries'][1])],['14'])
            self.assertEqual(verify_capture(result,path)['entries_checked'],2)

    def test_epub_manifest_preserves_separate_pdf_index_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); target=root/'out'; (target/'book-indexes').mkdir(parents=True)
            pdf=target/'book-indexes/pdf.json'; original='{"source":{"format":"pdf"},"entries":[]}\n';pdf.write_text(original)
            save(capture(self.make_book(root,'<p class="primary">Soup, 12</p>'),'fixture','Fixture','Author'),target)
            self.assertEqual(pdf.read_text(),original)
            self.assertEqual(json.loads((target/'book-indexes/manifest.json').read_text())['book_count'],1)

    def test_batch6_index_levels_are_scoped_to_the_source_edition(self):
        mappings=[('la-son-roy-choi-tien-nguyen-natasha-phan','ind1','ind2'),
                  ('meathead-rux-martin','Main','Sub-1'),
                  ('the-curry-guy-bible-dan-toombs','ind1','ind2'),
                  ('the-food-of-sichuan-fuchsia-dunlop','ind','ind1')]
        for book,root,child in mappings:
            with self.subTest(book=book), tempfile.TemporaryDirectory() as folder:
                result=capture(self.make_book(Path(folder),f'<p class="{root}">Eggplant</p><p class="{child}">Salad, 12</p>'),book,'Fixture','Author')
                self.assertEqual(result['entries'][1]['hierarchy'],['Eggplant','Salad'])
                self.assertEqual(result['entries'][1]['source_text'],'Salad, 12')

    def test_plenty_index_class_mapping_is_book_scoped(self):
        with tempfile.TemporaryDirectory() as folder:
            path=self.make_book(Path(folder),'<p class="index1">Artichokes</p><p class="index2">Gratin</p>')
            result=capture(path,'plenty-yotam-ottolenghi','Plenty','Author')
            other=capture(path,'fixture','Fixture','Author')
        self.assertEqual(result['entries'][1]['hierarchy'],['Artichokes','Gratin'])
        self.assertEqual(result['entries'][0]['level'],0)
        self.assertEqual(other['entries'][0]['level'],1)

    def test_eat_up_new_zealand_flat_index_hierarchy(self):
        with tempfile.TemporaryDirectory() as folder:
            result=capture(self.make_book(Path(folder),'<p class="Index-1">almonds</p><p class="Index-2">almond cream 270</p>'),'fixture','Fixture','Author')
        self.assertEqual(len(result['entries']),2)
        self.assertEqual(result['entries'][1]['hierarchy'],['almonds','almond cream'])
        self.assertEqual(result['entries'][1]['source_text'],'almond cream 270')

    def test_peternell_flat_indexs_entries_remain_children_with_original_locators(self):
        with tempfile.TemporaryDirectory() as folder:
            result=capture(self.make_book(Path(folder),'<p class="index">Almonds</p><p class="indexs">Tuiles, <i>304–5</i></p>'),'fixture','Fixture','Author')
        self.assertEqual(len(result['entries']),2)
        self.assertEqual(result['entries'][1]['hierarchy'],['Almonds','Tuiles'])
        self.assertEqual(result['entries'][1]['source_text'],'Tuiles, 304–5')

    def make_book(self, root, index, chapter='', nonspine=True):
        path=root/'book.epub'
        with ZipFile(path,'w') as z:
            z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
            z.writestr('book.opf','<package><manifest><item id="c" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
                       '<item id="i" href="index.xhtml" media-type="application/xhtml+xml"/></manifest>'
                       '<spine><itemref idref="c"/>'+('' if nonspine else '<itemref idref="i"/>')+'</spine></package>')
            z.writestr('index.xhtml','<html><body>'+index+'</body></html>')
            z.writestr('chapter.xhtml','<html><body>'+chapter+'</body></html>')
        return path

    def test_nonspine_index_raw_bytes_nested_hierarchy_and_cycles(self):
        index='<h1>Index</h1><ul class="index"><li><p id="pork">Pork. See also <a href="#ham">Ham</a></p>'
        index+='<ul><li><p>Toast, <a href="chapter.xhtml#page_12">12</a></p></li></ul></li>'
        index+='<li><p id="ham">Ham. See <a href="#pork">Pork</a></p></li></ul>'
        with tempfile.TemporaryDirectory() as folder:
            path=self.make_book(Path(folder),index,'<span role="doc-pagebreak" id="page_12" title="12"/>')
            result=capture(path,'fixture','Fixture','Author')
            self.assertEqual(len(result['entries']),3)
            self.assertEqual(result['entries'][1]['hierarchy'],['Pork','Toast'])
            self.assertEqual(result['entries'][0]['cross_references'][0]['relation'],'see_also')
            self.assertEqual(result['entries'][2]['cross_references'][0]['relation'],'see')
            self.assertEqual(result['entries'][0]['cross_references'][0]['target_entry_ids'],[result['entries'][2]['id']])
            with ZipFile(path) as z:
                self.assertEqual(base64.b64decode(result['documents'][0]['raw_base64']),z.read('index.xhtml'))
            self.assertEqual(result,capture(path,'fixture','Fixture','Author'))
            self.assertEqual(verify_capture(result,path)['entries_checked'],3)
            save(result,Path(folder)/'output')
            save(result,Path(folder)/'output')
            self.assertEqual(json.loads((Path(folder)/'output/book-indexes/manifest.json').read_text())['book_count'],1)

    def test_page_start_never_becomes_verified_recipe_or_pdf_page(self):
        index='<p class="primary">Soup, <a href="chapter.xhtml#page_12">12</a></p><p class="primary"><a href="chapter.xhtml#recipe">Soup</a></p><p class="primary"><em><a href="chapter.xhtml#photo">photo</a></em></p>'
        chapter='<h2 id="recipe"><span role="doc-pagebreak" id="page_12" title="12"/>Soup</h2><p id="photo">Picture</p>'
        recipes=[dict(id='r',blocks=[dict(kind='title',path='chapter.xhtml',anchors=['recipe','page_12'])])]
        with tempfile.TemporaryDirectory() as folder:
            result=capture(self.make_book(Path(folder),index,chapter),'fixture','Fixture','Author',recipes)
        links=[e['links'][0] for e in result['entries']]
        self.assertEqual(links[0]['printed_page'],'12');self.assertIsNone(links[0]['pdf_page'])
        self.assertEqual(links[0]['source_anchor_recipe_candidate_ids'],[])
        self.assertEqual(links[1]['source_anchor_recipe_candidate_ids'],['r'])
        self.assertEqual(links[2]['locator_kind'],'photograph')
        self.assertTrue(all(l['verified_recipe_ids']==[] for l in links))

    def test_flat_topic_only_unlinked_locator_and_broken_link_retained(self):
        index='<p class="main">Apples</p><p class="sub">Pie, 12–14, 20</p><p class="main">Bacon. See pork</p><p class="main"><a href="chapter.xhtml#missing">Missing</a></p><p>OceanofPDF.com</p>'
        with tempfile.TemporaryDirectory() as folder:
            result=capture(self.make_book(Path(folder),index),'fixture','Fixture','Author')
        self.assertEqual(result['entries'][1]['hierarchy'],['Apples','Pie'])
        self.assertEqual(result['entries'][1]['printed_locators'][0]['source_text'],'12–14, 20')
        self.assertEqual(result['entries'][2]['cross_references'][0]['status'],'unresolved_text')
        self.assertEqual(result['entries'][3]['links'][0]['status'],'missing_anchor')
        self.assertEqual(result['report']['text_nodes']['excluded_source_watermark'],1)

    def test_missing_index_is_explicit_and_wrong_edition_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'no-index.epub'
            with ZipFile(path,'w') as z:
                z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
                z.writestr('book.opf','<package><manifest><item href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest></package>')
                z.writestr('chapter.xhtml','<html><body><p>Soup.</p></body></html>')
            result=capture(path,'fixture','Fixture','Author')
            self.assertEqual(result['report']['status'],'no_index_found_in_epub')
            with self.assertRaisesRegex(ValueError,'edition'):
                capture(path,'fixture','Fixture','Author',expected_sha256='different')

    def test_unsupported_index_retains_text_without_inventing_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            result=capture(self.make_book(Path(folder),'<h1>Index</h1><section>Some unfamiliar layout, 12</section>'),'fixture','Fixture','Author')
        self.assertEqual(result['report']['status'],'retained_unparsed_index')
        self.assertEqual(result['entries'],[])
        self.assertTrue(any('unfamiliar' in n['text'] for n in result['documents'][0]['text_nodes']))

    def test_navigation_link_semantics_do_not_make_toc_an_index(self):
        from lxml import html
        from cookbook.epub_index import discover_indexes
        toc=html.fromstring('<html><body><nav><ol><li><a epub:type="index" href="index.xhtml">Index</a></li><li><a href="ch.xhtml">Soup</a></li></ol></nav></body></html>')
        self.assertEqual(discover_indexes({'nav.xhtml':toc},set()),{})

    def test_original_import_workflow_always_writes_index_artifacts(self):
        from cookbook.importer import BOOKS, import_library
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'sources';source.mkdir()
            layouts={'dinner': '<h2 class="h2r">Soup</h2><p class="ing">1 cup water</p><p class="step">Boil.</p>',
                     'braising': '<h2 class="recipe-title">Soup</h2><p class="recipe-ingred">1 cup water</p><p class="ded1">Boil.</p>',
                     'king': '<h2 class="rt">Soup</h2><p class="ril">1 cup water</p><p class="rp">Boil.</p>'}
            for book,(stem,_,_) in BOOKS.items():
                path=self.make_book(source,'<p class="primary">Soup, 12</p>',layouts[book])
                path.rename(source/('_OceanofPDF.com_'+stem+'.epub'))
            import_library(source,root/'runtime')
            manifest=json.loads((root/'runtime/book-indexes/manifest.json').read_text())
            self.assertEqual(manifest['book_count'],3)
            self.assertEqual(manifest['entries'],3)

    def test_implicit_page_and_duplicate_anchor_remain_uncertain(self):
        index='<p class="primary"><a href="chapter.xhtml#p12">Soup</a></p><p class="primary"><a href="chapter.xhtml#recipe">Stew</a></p>'
        chapter='<h2 id="p12">Soup</h2><h2 id="recipe">Stew</h2><h2 id="recipe">Another dish</h2>'
        recipes=[dict(id='r',blocks=[dict(kind='title',path='chapter.xhtml',anchors=['p12','recipe'])])]
        with tempfile.TemporaryDirectory() as folder:
            result=capture(self.make_book(Path(folder),index,chapter),'fixture','Fixture','Author',recipes)
        links=[e['links'][0] for e in result['entries']]
        self.assertEqual(links[0]['destination_kind'],'possible_page_anchor')
        self.assertEqual(links[1]['status'],'ambiguous_anchor')
        self.assertTrue(all(l['source_anchor_recipe_candidate_ids']==[] for l in links))
