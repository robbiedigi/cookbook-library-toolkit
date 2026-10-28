import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lxml import html
from cookbook.epub_batch import extract, source_blocks, link_records, VERSION


class EpubBatchTests(unittest.TestCase):
    def test_reviewed_ingredient_css_variants_and_headings(self):
        r,h,_=self.run_extract('the-wok-recipes-and-techniques-j-kenji-lopez-alt',[
            ('OEBPS/xhtml/Chapter222.xhtml','<p class="chap_hd">Invented pancake</p><p class="ing-list5">4 ounces bacon</p><p class="ing-list">2 eggs</p><p class="ing-listty">Rice, for serving</p><p class="dir">DIRECTIONS</p><p class="numberg">Cook.</p>')])
        self.assertFalse(h)
        self.assertEqual(r[0]['ingredients'],['4 ounces bacon','2 eggs','Rice, for serving'])
        r,h,_=self.run_extract('the-food-lab-better-home-cooking-through-j-kenji-lopez-alt',[
            ('text/part0009_split_036.html','<p class="recipe_rt">Invented steak</p><p class="recipe_ih1">For the steak</p><p class="recipe_i-spc">1 flank steak</p><p class="recipe_i">Salt</p><p class="recipe_rsteps">Grill.</p>')])
        self.assertFalse(h)
        self.assertEqual(r[0]['ingredients'],['1 flank steak','Salt'])
        self.assertEqual(r[0]['blocks'][1]['kind'],'ingredient_group')
        r,h,_=self.run_extract('the-food-lab-better-home-cooking-through-j-kenji-lopez-alt',[
            ('text/part0011_split_015.html','<p class="recipe_rt">EASY GRILLED NAAN-STYLE FLATBREAD</p><p class="recipe_i">Flour</p><p class="srecipe_i">Butter</p><p class="recipe_rsteps">Grill.</p>'),
            ('text/part0011_split_009.html','<p class="recipe_rt">BASIC HOMEMADE SAUSAGE</p><p class="recipe_i">Pork</p><p class="srecipe_i">Garlic for a different seasoning</p><p class="recipe_rsteps">Cook.</p>')])
        self.assertFalse(h)
        by_title={x['title']:x for x in r}
        self.assertEqual(by_title['EASY GRILLED NAAN-STYLE FLATBREAD']['ingredients'],['Flour','Butter'])
        self.assertEqual(by_title['BASIC HOMEMADE SAUSAGE']['ingredients'],['Pork'])

    def test_table_quantities_and_components_preserve_cells_and_order(self):
        r,h,_=self.run_extract('hot-thai-kitchen-pailin-chongchitnant',[
            ('OEBPS/Chon_9780449017067_epub_c05_r1.htm','<p class="recipe_title">Curry</p><table class="ing"><tr><td>½ cup</td><td>milk</td></tr><tr><td>2</td><td>chiles</td></tr></table><p class="method">Simmer.</p><p class="subrecipe_title">Paste</p><table class="tab1"><tr><td>1</td><td>shallot</td></tr></table><p class="method">Grind.</p>')])
        self.assertFalse(h)
        self.assertEqual([x['ingredients'] for x in r],[['½ cup milk','2 chiles'],['1 shallot']])
        self.assertEqual(r[0]['blocks'][1]['rows'],[['½ cup','milk'],['2','chiles']])
        self.assertEqual(r[0]['blocks'][1]['text'],'½ cup | milk\n2 | chiles')

    def test_fixed_layout_runs_keep_word_spacing_coordinates_and_page_label(self):
        doc=html.fromstring('<html><body><div id="TextContainer14"><span class="s1">PAGE 15</span><div><span class="s1" style="left:10px;bottom:20px">2 <span>cups </span>milk</span></div><span class="s1">Heat.</span></div></body></html>')
        blocks=source_blocks('page014.xhtml',doc,fixed_layout_spans=True)
        self.assertEqual([b['text'] for b in blocks],['PAGE 15','2 cups milk','Heat.'])
        self.assertTrue(all(b['pages']==['15'] for b in blocks))
        self.assertEqual(blocks[1]['layout_style'],'left:10px;bottom:20px')

    def test_french_equipment_and_master_dependency_are_not_primary_recipes(self):
        r,h,_=self.run_extract('mastering-the-art-of-french-cooking-volume-1-julia-child',[
            ('OEBPS/Chil_9780307958174_epub_c01_r1.htm','<h2 class="section">Soup</h2><p class="hanging">A 3-quart saucepan</p><p class="hanging">2 cups milk heated in a saucepan</p><p class="nonindent">Simmer.</p><h3 class="section">Variation</h3><p class="hanging">Ingredients for the soup</p><p class="nonindent">Chill.</p>')])
        self.assertEqual(r[0]['ingredients'],['2 cups milk heated in a saucepan'])
        self.assertEqual(r[0]['blocks'][1]['kind'],'equipment')
        self.assertEqual(len(h),1)
        self.assertIn('master recipe',h[0]['warnings'][0])

    def test_reviewed_duplicate_selection_rejects_changed_file(self):
        import hashlib
        from cookbook.epub_batch import selected_epub
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('small.epub','large.epub'):(root/name).write_bytes(name.encode())
            book=dict(id='test',title='Test',files=[dict(name=n,format='epub') for n in ('small.epub','large.epub')],edition_review=dict(status='resolved',preferred_epub='small.epub',files=[dict(name=n,sha256=hashlib.sha256((root/n).read_bytes()).hexdigest()) for n in ('small.epub','large.epub')]))
            self.assertEqual(selected_epub(book,root),root/'small.epub')
            (root/'large.epub').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'source changed'):selected_epub(book,root)


    def test_sichuan_misstyled_title_and_variation_do_not_merge_ingredients(self):
        r,h,_=self.run_extract('the-food-of-sichuan-fuchsia-dunlop',[
            ('OEBPS/Text/15_Chapter10_split_003.xhtml','<p class="recipes_rh">Fish-fragrant Eggplants</p><p class="recipes_ing1">1 eggplant</p><p class="recipes_recipe-tx">Fry.</p><p class="noindenth">Dry-fried Eggplants</p><p class="recipes_ing1">2 eggplants</p><p class="recipes_recipe-tx">Stir-fry.</p><p class="recipes_recipe-tx-ul">Sweet leaf-wrapped dumplings</p><p class="recipes_ing1">1 cup flour</p><p class="recipes_recipe-tx-ital">Mix.</p>')])
        self.assertFalse(h)
        self.assertEqual([x['title'] for x in r],['Fish-fragrant Eggplants','Dry-fried Eggplants'])
        self.assertEqual(r[0]['ingredients'],['1 eggplant'])
        self.assertEqual(r[1]['ingredients'],['2 eggplants'])
        self.assertEqual(r[1]['instructions'],['Stir-fry.'])
        self.assertEqual(r[1]['blocks'][-2]['kind'],'supplement_ingredient')

    def test_ratio_misstyled_quantity_is_not_a_method_and_original_is_retained(self):
        r,h,_=self.run_extract('ratio-michael-ruhlman',[
            ('OEBPS/9781416566120_c01.html','<p class="extractHead">Cake</p><p class="extractTextNoIndent">A classic cake.</p><p class="extractVerse">8 ounces sugar</p><p class="extractTextNoIndent">8 ounces eggs (4 large eggs plus 1 large yolk)</p><p class="extractVerse">8 ounces flour</p><p class="extractText">Bake.</p>')])
        self.assertFalse(h)
        self.assertEqual(r[0]['headnote'],'A classic cake.')
        self.assertEqual(len(r[0]['ingredients']),3)
        self.assertEqual(r[0]['instructions'],['Bake.'])

    def test_jaffrey_split_title_preserves_each_source_block(self):
        r,h,_=self.run_extract('an-invitation-to-indian-cooking-a-cookbook-madhur-jaffrey',[
            ('OEBPS/xhtml/c01.xhtml','<p class="rt-supb-pg">Mulligatawny</p><p class="rt">soup</p><p class="ry">Serves 4</p><p class="ril">2 cups broth</p><p class="rp">Simmer.</p><p class="rt-supb-pg">White</p><p class="rt">soup</p><p class="ril">1 cup milk</p><p class="rp">Warm.</p>')])
        self.assertFalse(h)
        self.assertEqual([x['title'] for x in r],['Mulligatawny soup','White soup'])
        self.assertEqual([b['text'] for b in r[0]['blocks'][:2]],['Mulligatawny','soup'])
        self.assertIn('Mulligatawny\nsoup',r[0]['source_text'])

    def test_meathead_interleaved_sidebar_and_notes_do_not_truncate_method(self):
        r,h,_=self.run_extract('meathead-rux-martin',[
            ('OEBPS/text/p222.xhtml','<h2 class="RT">Whole Hog Pig Pickin’</h2><h2 class="SIDEBAR-HEADS">Ordering the Hog</h2><p class="MYTH-TEXT">Ask your butcher.</p><li class="ING">1 hammer</li><li class="ING">1 (75-pound) hog (see below)</li><li class="ING">3 cups kosher salt</li><p class="STEPS">Season.</p><p class="NOTE">Keep cold.</p><p class="STEPS">Cook.</p>')])
        self.assertFalse(h)
        self.assertEqual(r[0]['ingredients'],['1 (75-pound) hog (see below)','3 cups kosher salt'])
        self.assertEqual(r[0]['instructions'],['Season.','Cook.'])
        self.assertEqual(r[0]['blocks'][3]['kind'],'equipment')
        self.assertIn('Ask your butcher.',r[0]['source_text'])

    def test_curry_subtitle_is_not_a_chapter_boundary(self):
        r,h,_=self.run_extract('the-curry-guy-bible-dan-toombs',[
            ('OEBPS/chapter05.xhtml','<p class="rep-ttl">ANA CHINGRI</p><p class="h1 ntop">(PRAWN WITH PINEAPPLE)</p><p class="hang">4 prawns</p><p class="tx2">Cook.</p><p class="h1">EQUIPMENT AND PRESENTATION</p><p class="tx">Unrelated prose.</p>')])
        self.assertFalse(h)
        self.assertEqual(r[0]['instructions'],['Cook.'])
        self.assertIn('(PRAWN WITH PINEAPPLE)',r[0]['source_text'])
        self.assertNotIn('Unrelated prose.',r[0]['source_text'])

    def test_flavor_explanation_is_distinct_from_group_and_method(self):
        r,h,_=self.run_extract('the-flavor-equation-nik-sharma',[
            ('OPS/xhtml/019-chapter007.xhtml','<h1 class="section_title">Salad</h1><p class="yield">Serves 2</p><p class="first1_noindent">For the dressing:</p><p class="ingred_sublist">1 lime</p><p class="sidebar2_hd">THE FLAVOR APPROACH</p><p class="first2_noindent">Lime gives acidity.</p><p class="method_txt">Squeeze.</p><p class="first1_noindent">Serve.</p>')])
        self.assertFalse(h)
        self.assertEqual(r[0]['ingredients'],['1 lime'])
        self.assertEqual(r[0]['instructions'],['Squeeze.','Serve.'])
        self.assertEqual(r[0]['blocks'][2]['kind'],'ingredient_group')
        self.assertIn('Lime gives acidity.',r[0]['source_text'])

    def test_plenty_preserves_explicit_mixed_fraction_without_a_unit(self):
        r,h,report=self.run_extract('plenty-yotam-ottolenghi',[
            ('OPS/005-chapter1.html','<h1>Roots</h1><h2 class="left">Salad</h2><p class="noindent">A fresh salad.</p><h4>Serves 2</h4><p class="hang">Dressing</p><p class="hang">1<sup class="frac">1</sup>/<sub class="frac">2</sub> lemons</p><p class="noindent">Squeeze.</p><h1>Another chapter</h1><p class="noindent">Unrelated essay.</p>')])
        self.assertFalse(h)
        self.assertEqual(r[0]['ingredients'],['1 1/2 lemons'])
        self.assertEqual(r[0]['yield'],'Serves 2')
        self.assertEqual(r[0]['instructions'],['Squeeze.'])
        self.assertIn('11/2 lemons',r[0]['source_text'])
        self.assertNotIn('Unrelated essay',r[0]['source_text'])
        self.assertIn('explicit superscript',r[0]['normalizations'][0]['rule'])

    def test_turshen_misstyled_intro_and_methods_keep_recipe_boundary(self):
        r,h,_=self.run_extract('what-goes-with-what-100-recipes-julia-turshen',[
            ('OEBPS/xhtml/chapter11.xhtml','<p class="RECIPE-HEAD">CARROT GINGER SOUP</p><p class="RECIPE-HEAD">Packed with flavor from both fresh and dried ginger, this soup is comforting.</p><p class="SERVING-SIZE">Serves 4</p><p class="SERVING-SIZE">For the soup:</p><p class="INGREDIENTS">2 carrots</p><p class="SERVING-SIZE">First, cook the carrots.</p><p class="RECIPE-TEXT">Puree.</p>')])
        self.assertFalse(h)
        self.assertEqual(len(r),1)
        self.assertEqual(r[0]['title'],'CARROT GINGER SOUP')
        self.assertIn('Packed with flavor',r[0]['headnote'])
        self.assertEqual(r[0]['yield'],'Serves 4')
        self.assertEqual(r[0]['instructions'],['First, cook the carrots.','Puree.'])
        self.assertEqual(r[0]['blocks'][3]['kind'],'ingredient_group')

    def test_oaxaca_variations_keep_separate_ingredients_and_method(self):
        r,h,_=self.run_extract('oaxaca-bricia-lopez-javier-cabral',[
            ('OEBPS/xhtml/ch02.xhtml','<p class="h3">Chilaquiles</p><p class="noindent">A family dish.</p><p class="ingred">10 tortillas</p><p class="noindent1">Fry.</p><p class="noindent">Drain.</p><p class="noindent1b">CHILAQUILES VERDES</p><p class="ingredt">2 pounds tomatillos</p><p class="noindentt">Simmer.</p>')])
        self.assertFalse(h)
        self.assertEqual(r[0]['ingredients'],['10 tortillas'])
        self.assertEqual(r[0]['instructions'],['Fry.','Drain.'])
        self.assertEqual(r[0]['blocks'][-2]['kind'],'supplement_ingredient')
        self.assertEqual(r[0]['blocks'][-1]['kind'],'note')

    def test_jerusalem_tahini_method_uses_headnote_style_after_ingredients(self):
        r,h,_=self.run_extract('jerusalem-yotam-ottolenghi',[
            ('OEBPS/Otto_9781607743958_epub_c09_r1.htm','<h1 class="recipe_title">Tahini sauce</h1><div class="headnote">An everyday sauce.</div><div class="IL_item">1 cup tahini</div><div class="headnote">Stir.</div>')])
        self.assertFalse(h)
        self.assertEqual(r[0]['headnote'],'An everyday sauce.')
        self.assertEqual(r[0]['instructions'],['Stir.'])

    def test_ottolenghi_observed_method_before_list_is_title_scoped(self):
        r,h,_=self.run_extract('ottolenghi-yotam-ottolenghi',[
            ('OEBPS/Otto_9781607744191_epub_c04_r1.htm','<h1 class="recipe_title">Vanilla extract</h1><div class="headnote">Split and simmer.</div><div class="IL_item">4 vanilla beans</div><h1 class="recipe_title">A different recipe</h1><div class="headnote">A story.</div><div class="IL_item">1 pear</div>')])
        self.assertEqual(r[0]['instructions'],['Split and simmer.'])
        self.assertEqual(h[0]['instructions'],[])

    def test_section_target_belongs_to_matching_title_not_its_subrecipe(self):
        r,h,report=self.run_extract('a-recipe-for-cooking-cal-peternell',[
            ('OEBPS/text/nav.xhtml','<p class="toc"><a href="9780062427878_Rec_1.xhtml#rec1">Tart</a></p>'),
            ('OEBPS/text/9780062427878_Rec_1.xhtml','<section id="rec1" title="Tart"><header><p class="h2r"><span id="page_14"/><span id="page_15"/>Tart</p></header><p class="ing">1 round dough</p><p class="noindent1">Bake.</p><p class="noindentl1">Serve warm.</p><p class="h4st" id="dough">Dough</p><p class="ing">1 cup flour</p><p class="noindent1">Mix.</p></section>')])
        self.assertFalse(h);self.assertEqual(len(r),2)
        self.assertEqual(r[0]['source']['printed_page'],'15')
        self.assertEqual(r[0]['source']['anchor'],'rec1')
        self.assertNotIn('rec1',r[1]['blocks'][0]['anchors'])
        self.assertEqual(report['toc_matched'][0]['recipe_id'],r[0]['id'])
        self.assertEqual(r[0]['instructions'],['Bake.'])
        self.assertEqual(r[1]['ingredients'],['1 cup flour'])

    def test_simple_too_nested_method_and_fundamentals_preserve_first_ingredient(self):
        r,h,_=self.run_extract('ottolenghi-simple-too-a-cookbook-yotam-ottolenghi',[
            ('OEBPS/xhtml/139_rec_Sauce.xhtml','<div class="rt"><h2 class="rt_rule-below">Sauce</h2></div><p class="rhn">Keeps for 5 days.</p><p class="ry">Makes 1 cup,</p><p class="ry">enough for 4</p><p class="rilf">1 cup herbs</p><p class="ril">1 tbsp oil</p><ol><li class="rp"><p class="rp">Chop.</p><p class="rp">Mix.</p></li></ol>')])
        self.assertFalse(h);self.assertEqual(len(r),1)
        self.assertEqual(r[0]['ingredients'],['1 cup herbs','1 tbsp oil'])
        self.assertEqual(r[0]['instructions'],['Chop.','Mix.'])
        self.assertEqual(r[0]['headnote'],'Keeps for 5 days.')
        self.assertEqual(r[0]['yield'],'Makes 1 cup, enough for 4')

    def test_peternell_shared_prose_is_method_after_method_start(self):
        r,h,_=self.run_extract('a-recipe-for-cooking-cal-peternell',[
            ('OEBPS/text/9780062427878_Rec_1.xhtml','<p class="h2r">Cabbage</p><p class="noindent">A winter dish.</p><p class="ing">1 cabbage</p><p class="noindent">Trim.</p><p class="noindent1">Boil.</p><p class="noindent">Stuff.</p><p class="noindentl1">Try greens instead.</p>')])
        self.assertFalse(h)
        self.assertEqual(r[0]['headnote'],'A winter dish.')
        self.assertEqual(r[0]['instructions'],['Trim.','Boil.','Stuff.'])

    def test_inline_page_id_without_role_overrides_previous_page(self):
        doc=html.fromstring('<div><a id="page_13"/><h2><!-- marker --><a id="page_14"/>Soup</h2><p><span id="page_15" title="15"/>Boil.</p></div>')
        blocks=source_blocks('a.xhtml',doc,inline_page_ids=True)
        self.assertEqual([b['pages'] for b in blocks],[['14'],['15']])

    def test_source_image_heading_stops_unrelated_sidebar_entering_recipe(self):
        r,h,report=self.run_extract('sweet-enough-a-dessert-cookbook-alison-roman',[
            ('OEBPS/xhtml/c05.xhtml','<div><h2 class="rt">Frozen yogurt</h2><p class="ril">1 cup yogurt</p><p class="rp">Freeze.</p><img alt="creamsicle" src="heading.jpg"/><p class="sbpf">A separate prose recipe.</p><p class="sbp">Whip cream.</p></div>')])
        self.assertFalse(h)
        self.assertNotIn('Whip cream.',r[0]['source_text'])
        section=next(s for s in report['supporting_sections'] if s['title']=='creamsicle')
        self.assertIn('Whip cream.',[b['text'] for b in section['blocks']])

    def test_shared_prose_class_changes_role_only_at_method_start(self):
        r,h,_=self.run_extract('falastin-a-cookbook-9780399581748-yotam-frw-tamimi-sami-wigley-tara-ot',[
            ('OEBPS/xhtml/c01.xhtml','<div><p class="rt">Eggs</p><p class="rp">A family favorite.</p><p class="ril">2 eggs</p><p class="rpf">Boil.</p><p class="rp">Peel.</p></div>')])
        self.assertFalse(h)
        self.assertEqual(r[0]['headnote'],'A family favorite.')
        self.assertEqual(r[0]['instructions'],['Boil.','Peel.'])

    def test_vietnamese_method_separator_and_variation_boundary(self):
        r,h,_=self.run_extract('vietnamese-food-any-day-andrea-nguyen',[
            ('OEBPS/c01_split_001.xhtml','<div><p class="rt">Dressing</p><p class="rhnf">A tangy dressing.</p><p class="spacer">⁄</p><p class="ril">1 lime</p><p class="spacer">⁄</p><p class="rhnf">Squeeze.</p><p class="rhnf">Stir.</p><p class="r1t">Quick slaw</p><p class="rpf">Dress cabbage.</p></div>')])
        self.assertFalse(h)
        self.assertEqual(r[0]['instructions'],['Squeeze.','Stir.'])
        self.assertEqual(r[0]['headnote'],'A tangy dressing.')
        self.assertEqual(r[0]['blocks'][-1]['kind'],'note')

    def test_plenty_more_wrapper_separates_intro_from_same_class_method(self):
        r,h,_=self.run_extract('plenty-more-yotam-ottolenghi',[
            ('text/part0005_split_001.html','<div><h2 class="recipe_header">Salad</h2><li class="calibre7">1 tomato</li><div class="ingredients_list"><p class="calibre8">A favorite salad.</p></div><p class="calibre8">Chop.</p><figure><img src="salad.jpg"/><figcaption>Salad photo</figcaption></figure></div>')])
        self.assertFalse(h)
        self.assertEqual(r[0]['instructions'],['Chop.'])
        self.assertEqual(r[0]['headnote'],'A favorite salad.')
        self.assertEqual(r[0]['blocks'][-1]['text'],'Salad photo')

    def test_focaccia_topping_does_not_change_primary_ingredients(self):
        r,h,_=self.run_extract('dessert-person-claire-saffitz',[
            ('OEBPS/xhtml/c06.xhtml','<div><p class="rt">Bread</p><p class="ril">1 cup flour</p><p class="rpf">Bake.</p><p class="RT">Variation: tomato topping</p><p class="ril">1 tomato</p><p class="rp">Add tomato.</p></div>')])
        self.assertFalse(h)
        self.assertEqual(r[0]['ingredients'],['1 cup flour'])
        self.assertEqual(r[0]['instructions'],['Bake.'])
        self.assertIn('Add tomato.',r[0]['source_text'])

    def test_pilot_commands_cannot_overwrite_expanded_corpus(self):
        from cookbook.importer import import_library
        from cookbook.knowledge import import_knowledge
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            recipes='{"book_id":"new-book"}\n'
            sections='{"source":{"book_id":"new-book"}}\n'
            (root/'recipes.jsonl').write_text(recipes)
            (root/'knowledge_sections.jsonl').write_text(sections)
            with self.assertRaisesRegex(ValueError,'would discard'):
                import_library(root/'missing',root)
            with self.assertRaisesRegex(ValueError,'would discard'):
                import_knowledge(root/'missing',root)
            self.assertEqual((root/'recipes.jsonl').read_text(),recipes)
            self.assertEqual((root/'knowledge_sections.jsonl').read_text(),sections)

    def test_method_list_item_with_nested_note_retains_both_once(self):
        doc=html.fromstring('<ul><li class="rp">Decorate with sprinkles.<p class="rn">Use a tray.</p></li></ul>')
        blocks=source_blocks('a.xhtml',doc)
        self.assertEqual([b['text'] for b in blocks],['Decorate with sprinkles.','Use a tray.'])

    def test_leaf_divs_tables_and_inline_assets_are_not_lost(self):
        doc=html.fromstring('<body><div><h2 class="recipe_title" id="r">Soup</h2><div class="ingredients"><div class="IL_item">1½ cups water</div></div><div class="method">Boil, then cool.</div><table><tr><td>Cold</td><td>½ cup</td></tr></table><img src="figure.jpg" alt="Diagram"/></div></body>')
        blocks=source_blocks('text/a.xhtml',doc)
        self.assertEqual([b['text'] for b in blocks],['Soup','1½ cups water','Boil, then cool.','Cold | ½ cup','Diagram'])
        self.assertEqual(blocks[-1]['assets'],['text/figure.jpg'])
        self.assertEqual(blocks[-2]['rows'],[['Cold','½ cup']])

    def test_printed_page_marker_on_title_is_used(self):
        doc=html.fromstring('<div><p><span role="doc-pagebreak" title="10"/></p><h2><span role="doc-pagebreak" title="11"/>Soup</h2></div>')
        self.assertEqual(source_blocks('a.xhtml',doc)[0]['pages'],['11'])

    def run_extract(self, book_id, docs):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);(path/'book.epub').write_bytes(b'synthetic source fixture')
            book=dict(id=book_id,title='Fixture',author='Invented Author',files=[dict(name='book.epub',format='epub')])
            with patch('cookbook.epub_batch.read_epub',return_value=[(m,html.fromstring(d)) for m,d in docs]):
                return extract(book,path)

    def test_vegetable_heading_ends_recipe_and_prose_is_held(self):
        r,h,report=self.run_extract('vegetable-literacy-deborah-madison',[('a.xhtml','<body><h2 class="recipe_title" id="r">Soup</h2><div class="IL_item">1 cup water</div><div class="method">Boil.</div><h2 class="veg_header">A different plant</h2><p>Unrelated knowledge.</p><h2 class="recipe_title">Simple tea</h2><div class="method">Steep leaves in water.</div></body>')])
        self.assertEqual(len(r),1);self.assertEqual(len(h),1)
        self.assertNotIn('Unrelated',r[0]['source_text'])
        self.assertEqual(h[0]['warnings'],['No separate primary ingredient list identified.'])

    def test_variation_keeps_own_ingredients_and_source_locations(self):
        r,h,_=self.run_extract('all-about-roasting-molly-stevens',[
            ('text/a_split_000.html','<body><p class="rec-tit">Roast</p><p class="rec_i">21/4 cups beans</p><p class="rec-step">Roast.</p></body>'),
            ('text/a_split_001.html','<body><p class="rec-opt">OPTION: Apples</p><p class="rec_i">1 apple</p><p class="rec-step">Add the apple.</p></body>')])
        self.assertFalse(h);self.assertEqual(r[0]['ingredients'],['2 1/4 cups beans'])
        self.assertEqual(r[0]['instructions'],['Roast.'])
        self.assertEqual(r[0]['blocks'][-2]['kind'],'supplement_ingredient')
        self.assertEqual(r[0]['blocks'][-1]['path'],'text/a_split_001.html')
        self.assertIn('21/4',r[0]['source_text']);self.assertEqual(len(r[0]['normalizations']),1)

    def test_recipe_does_not_absorb_next_chapter_and_links_resolve(self):
        r,h,report=self.run_extract('more-is-more-get-loose-in-the-kitchen-molly-baz',[
            ('toc.xhtml','<p class="toc2"><a href="a.xhtml">Soup</a></p>'),
            ('a.xhtml','<body><h1 class="red rt" id="a">Soup</h1><p class="ril">1 cup broth</p><p class="RECIPE-LIST">Heat; serve with <a href="b.xhtml#b">beans</a>.</p></body>'),
            ('chapter.xhtml','<p>Different chapter introduction.</p>'),
            ('b.xhtml','<body><h1 class="rt" id="b">Beans</h1><p class="ril">1 can beans</p><p class="RECIPE-LIST">Warm.</p></body>')])
        self.assertFalse(h);self.assertEqual(len(r),2)
        self.assertNotIn('Different chapter',r[0]['source_text'])
        self.assertEqual(len(report['toc_matched']),1)
        link_records(r);self.assertEqual(r[0]['references'][0]['recipe_id'],r[1]['id'])
        self.assertEqual(r[0]['source']['extractor'],VERSION)

    def test_dressing_first_ingredient_and_method_survive(self):
        r,h,_=self.run_extract('cook-this-book-molly-baz',[('a.xhtml','<body><p class="rt1">Dressing</p><p class="ril1f">½ cup yogurt</p><p class="ril1">1 lime</p><p class="rps">Whisk.</p><p class="rpsz">Season.</p></body>')])
        self.assertFalse(h);self.assertEqual(r[0]['ingredients'],['½ cup yogurt','1 lime']);self.assertEqual(r[0]['instructions'],['Whisk.','Season.'])
