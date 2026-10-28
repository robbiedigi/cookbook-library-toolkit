"""Reviewed EPUB layout adapters and candidate-only batch extraction.

These profiles describe observed publisher markup, not a universal recipe parser.
Unstructured or incomplete candidates stay out of the recipe index.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .importer import clean, normalize_fractions, read_epub, resolve
from .source_artifacts import is_source_artifact

VERSION = 'epub-batch-1'
# Explicit substantive spine ranges, inspected in these editions. Navigation,
# copyright, acknowledgments and indexes are excluded from passage extraction.
CONTENT_MEMBERS = {
    'a-recipe-for-cooking-cal-peternell': r'OEBPS/text/9780062427878_(?:Introduction|General1?|Chapter_\d+a?|Part_\d+|Rec_\d+|Chart|image[1-4])\.xhtml$',
    'ottolenghi-simple-too-a-cookbook-yotam-ottolenghi': r'OEBPS/xhtml/\d+_(?:fm_[12]|in_Introduction|c\d+_[^/]+|rec_[^/]+|bm_[12]_[^/]+)\.xhtml$',
    'all-about-roasting-molly-stevens': r'text/part(?:000[45789]|001[012])(?:_split_\d+)?\.html$',
    'cook-this-book-molly-baz': r'OEBPS/xhtml/(?:intro|c\d+[a-z]?)\.xhtml$',
    'more-is-more-get-loose-in-the-kitchen-molly-baz': r'OEBPS/xhtml/(?:intro|fm[1-6]|c\d+(?:_\d+)?)\.xhtml$',
    'dining-in-alison-roman': r'OEBPS/(?:fm0[234]|c\d+)\.xhtml$',
    'gjelina-travis-lett': r'OPS/xhtml/\d+-(?:introduction|chapter\d+|recipe\d+)\.html$',
    'six-seasons-joshua-mcfadden': r'OEBPS/(?:intro|goto|pickles|season[1-6])\.xhtml$',
    'vegetable-literacy-deborah-madison': r'OEBPS/Madi_9781607741923_epub_(?:itr|fm1|c\d+)_r1\.htm$',
    'ottolenghi-simple-yotam-ottolenghi': r'OPS/xhtml/(?:introduction|chapter\d+)\.xhtml$',
    'plenty-more-yotam-ottolenghi': r'text/part(?:000[4-9]|001[0-6])(?:_split_\d+)?\.html$',
    'falastin-a-cookbook-9780399581748-yotam-frw-tamimi-sami-wigley-tara-ot': r'OEBPS/xhtml/(?:intro|c\d+|bm1)\.xhtml$',
    'from-the-oven-to-the-table-diana-henry': r'OEBPS/xhtml/(?:fm01|ch\d+|bm01)\.xhtml$',
    'vietnamese-food-any-day-andrea-nguyen': r'OEBPS/(?:fm[123]|c\d+)(?:_split_\d+)?\.xhtml$',
    'sweet-enough-a-dessert-cookbook-alison-roman': r'OEBPS/xhtml/(?:intro|c\d+)\.xhtml$',
    'dessert-person-claire-saffitz': r'OEBPS/xhtml/(?:intro|fm[2-6]|c\d+)\.xhtml$',
}
PROFILES = {
    'a-recipe-for-cooking-cal-peternell': dict(titles='h2r h4st', ingredients='ing', instructions='noindent1', yields='make make0', groups='ing10 ing1', headnotes='noindent noindent2 indent', barriers='h2fc h2p', notes='noindentl1 noindentl'),
    'ottolenghi-simple-too-a-cookbook-yotam-ottolenghi': dict(titles='rt rt_rule-below', ingredients='ril rilf', instructions='rp rps', yields='ry', groups='rilh', headnotes='rhn', barriers='ct fmt bmt'),
    'cook-this-book-molly-baz': dict(titles='rt rt_nobreak rt_noim rt2 rt1', ingredients='ril ril1 ril1f rils', instructions='rp rp2 rpbl rpblf rpf rp1 rpf3 rps rpsz', yields='ry ry1', groups='rilh rilhf', headnotes='rhnf rhnf-alt rhn-alt', barriers='ct fmt'),
    'more-is-more-get-loose-in-the-kitchen-molly-baz': dict(titles='rt srt', ingredients='ril ril2 rilf', instructions='RECIPE-LIST RECIPE-LIST1 rph drink-bl drink-blf drink2-blf', yields='ry ry-cobalt ry-green ry-salmon ry-drink', groups='rilh', headnotes='rhnf rhn rhnf_space', barriers='subchap subchap_left rt_nobreak'),
    'gjelina-travis-lett': dict(titles='section_title', title_tags='h3', ingredients='ingred_sublist ingred_sublistt', instructions='method_txt', yields='yield', groups='', headnotes='recipe_hdnote', barriers='chapter_title chapter_hd'),
    'six-seasons-joshua-mcfadden': dict(titles='RH RH1', ingredients='RI RIB', instructions='RP RPB RPBR', yields='RY', groups='RIH', headnotes='RHN RHN1 RHN1B', barriers='CT CT2 CN H1 H2 H3 H4 FMH FMH1 FMSH CTOCH'),
    'vegetable-literacy-deborah-madison': dict(titles='recipe_title', ingredients='IL_item IL_item_space', instructions='method', yields='yield', groups='IL_subheader IL_subheader_top subrecipe_title', headnotes='headnote headnote1', barriers='veg_header chapter_title chapter_number headerA HeaderB veg_recipe_list_head'),
    'dining-in-alison-roman': dict(titles='rt h2a', ingredients='ril rilf rils', instructions='rp rpf rps', yields='ry ry2', groups='rilh rilhf rph', headnotes='rhnf rhn rhn2 rhnf1', barriers='h2 h3 bxt sbh sbt fmt'),
    'all-about-roasting-molly-stevens': dict(titles='rec-tit', ingredients='rec_i rec_i1', instructions='rec-step rec-step1 rec-step_', yields='ser', groups='rec_ih', headnotes='', barriers='part-title ch-title', notes='rec-opt rec-bh rec-bh1 rec-bh2 rec-bh1_'),
    'ottolenghi-simple-yotam-ottolenghi': dict(titles='rec_head', ingredients='ingredient_list', instructions='method', yields='ing_head', groups='ing_head1', headnotes='mc_intro mc_intro1 mc_intro2 mc_intro3', barriers='rec_list app_head int_head fm_head'),
    'plenty-more-yotam-ottolenghi': dict(titles='recipe_header', ingredients='calibre7', instructions='calibre8', yields='calorieCount', groups='calibre9', headnotes='', headnote_ancestors='ingredients_list', barriers='chapter-head chapter-head-left chapter-head-center'),
    'falastin-a-cookbook-9780399581748-yotam-frw-tamimi-sami-wigley-tara-ot': dict(titles='rt rtno', ingredients='ril rilf', instructions='rpf rp', method_start='rpf', shared_prose='rp', yields='ry', groups='rilh', headnotes='', barriers='ct h1 h1t bmt pcust1'),
    'from-the-oven-to-the-table-diana-henry': dict(titles='h2', ingredients='ing', instructions='method', yields='ser', groups='ing1', headnotes='intro', barriers='h2f'),
    'vietnamese-food-any-day-andrea-nguyen': dict(titles='rt', ingredients='ril rils', instructions='rpf', shared_prose='rhnf', yields='ry', groups='rilhf rilh rilh1', headnotes='rhnf', notes='rnh r1t', barriers='ct cn1 bxt fmh fmh1 fmh2 sbh'),
    'sweet-enough-a-dessert-cookbook-alison-roman': dict(titles='rt rt-alt', ingredients='ril rilf', instructions='rp rpf rph rphf', yields='ry', groups='rilh rilhf', headnotes='rhnf rhn', notes='rh1c rh1a rh1d', barriers='sbt cotxf cotx bmt', barrier_image_alts=('savories','the one, the only','toasted cake','cold cake','creamsicle','sundae bar','so you want to make jam','have dessert, will travel','how to')),
    'dessert-person-claire-saffitz': dict(titles='rt rt2', ingredients='ril rilf', instructions='rp rpf', yields='ry', groups='rilh rilhf', headnotes='rhnf', notes='RT rvh1', barriers='CT fmt fmt-alt bmt bmt1'),
}
BATCH_02_BOOKS = (
    'ottolenghi-simple-yotam-ottolenghi', 'plenty-more-yotam-ottolenghi',
    'falastin-a-cookbook-9780399581748-yotam-frw-tamimi-sami-wigley-tara-ot',
    'from-the-oven-to-the-table-diana-henry', 'vietnamese-food-any-day-andrea-nguyen',
    'sweet-enough-a-dessert-cookbook-alison-roman', 'dessert-person-claire-saffitz',
)
for _book_id in BATCH_02_BOOKS:
    PROFILES[_book_id].update(version='epub-batch-2', extra_block_tags=('figcaption',), restrict_members=True, inline_page_ids=True)
BATCH_03_BOOKS = ('a-recipe-for-cooking-cal-peternell', 'ottolenghi-simple-too-a-cookbook-yotam-ottolenghi')
for _book_id in BATCH_03_BOOKS:
    PROFILES[_book_id].update(version='epub-batch-3', extra_block_tags=('figcaption',), restrict_members=True, inline_page_ids=True)
PROFILES[BATCH_03_BOOKS[0]].update(section_title_anchors=True, last_inline_page=True,
                                shared_prose='noindent', method_start='noindent1', shared_prose_after_ingredients=True)
PROFILES[BATCH_03_BOOKS[1]].update(
    join_yields=True,
    bibliography=dict(title='Ottolenghi Simple Too', author='Yotam Ottolenghi; Verena Lochmuller'),
    bibliography_evidence='OEBPS/xhtml/003_crt_Copyright.xhtml: text copyright 2026 by Yotam Ottolenghi and Verena Lochmuller; title-page alt text credits Milli Taylor and Tara Wigley separately.')
PROFILES['vietnamese-food-any-day-andrea-nguyen']['method_separator'] = 'spacer'
PROFILES['ottolenghi-simple-yotam-ottolenghi']['nav_classes'] = 'li_normal'
PROFILES['falastin-a-cookbook-9780399581748-yotam-frw-tamimi-sami-wigley-tara-ot']['bibliography'] = dict(title='Falastin: A Cookbook', author='Sami Tamimi; Tara Wigley')
# Batch 4: independently inspected publisher layouts; older adapters stay pinned.
BATCH_04_PROFILES = {
 'jerusalem-yotam-ottolenghi': dict(titles='recipe_title', ingredients='IL_item', instructions='method_step', shared_prose='headnote', shared_prose_after_ingredients=True, yields='yield', groups='IL_subheader IL_subheader_top', headnotes='headnote headnote1 tips tips1', barriers='textbox_header headerA chapter_title'),
 'ottolenghi-yotam-ottolenghi': dict(titles='recipe_title', ingredients='IL_item IL_item_space', instructions='method_step method_nonindent method_day', yields='yield', groups='IL_subheader IL_subheader_top', headnotes='headnote headnote_indent', barriers='HeaderA HeaderA1 HeaderB chapter_title chapter_number'),
 'ottolenghi-flavor-a-cookbook-yotam-ottolenghi': dict(titles='rt rt-alt rtno rt-altno', ingredients='ril rils rilWays', instructions='rp rpf', yields='ry ryno Serving', groups='rilh', headnotes='rhnf rhn', barriers='ct h1Process h1Pairing h1Produce h2B', join_yields=True),
 'kismet-bright-fresh-vegetable-loving-recipes-sara-kramer': dict(titles='rt rt-alt rt-c2 rt-c3 rt-c4 rt-c5 rt-c6 rt-c7 rt-c8', ingredients='ril rilf', instructions='rp rpf rph rphf rps', yields='ry', groups='rilh rilhf', headnotes='rhnf rhn', barriers='ct fmt gt', notes='tip'),
 'nopalito-a-mexican-kitchen-gonzalo-guzman-stacy-adimando': dict(titles='rt rt2 r1t r1ta', ingredients='ril', instructions='rp rpf', yields='rh1', groups='rilh', headnotes='rhn rhnf rhn-middle', barriers='ct h1 h2 h3 rsbh sbt'),
 'oaxaca-bricia-lopez-javier-cabral': dict(titles='h3', ingredients='ingred ingredt ingredb', instructions='noindent1', shared_prose='noindent noindentt indent', method_start='noindent1', yields='', groups='', headnotes='chh', notes='noindent1b', barriers='h2n h2c fmh h3c', role_patterns=(('ingredt', '^VARIATION$', 'note'), ('noindentt', '^.*SERVES [0-9]', 'yield'))),
 'what-goes-with-what-100-recipes-julia-turshen': dict(titles='RECIPE-HEAD', ingredients='INGREDIENTS', instructions='BOLD-DIRECTION', shared_prose='RECIPE-TEXT', shared_prose_after_ingredients=True, yields='SERVING-SIZE SERVING-SIZE1', groups='', headnotes='', barriers='CT CT1 PT FMH RECIPE-HEAD1', non_title_pattern=r'^Packed with flavor from both fresh and dried ginger,', role_patterns=(('RECIPE-HEAD', '^Packed with flavor from both fresh and dried ginger,', 'headnote'), ('SERVING-SIZE SERVING-SIZE1', r'^(?:For .*|To finish):$', 'ingredient_group'), ('SERVING-SIZE SERVING-SIZE1', r'^(?!Serves |Makes )', 'instruction'))),
 'my-little-french-kitchen-rachel-khoo': dict(titles='chapter_title', ingredients='ingred_sublist ingred_sublistt', instructions='method_txt', yields='yield', groups='', headnotes='recipe_hdnote chapter_hd', barriers='section_title', notes='leftt left'),
 'i-dream-of-dinner-so-you-dont-have-to-ali-slagle': dict(titles='rt', ingredients='rilf ril', instructions='rpf rp rp2', yields='ry', groups='', headnotes='rhnf', barriers='ct pftext fmt fmh1 fmh2 bmt'),
 'eat-up-new-zealand-al-brown': dict(titles='recipeTitle', ingredients='ingredients', instructions='Steps Steps-1 Steps-0', yields='prepTime', groups='ingredients-subHead', headnotes='Recipe-intro Recipe-intro-1', barriers='introTitle introHead-1 introHead-1g introHead-1y introHead-1p introHead-1gg introHead-1r'),
}
CONTENT_MEMBERS.update({
 'jerusalem-yotam-ottolenghi': r'OEBPS/Otto_9781607743958_epub_(?:itr|fm[1-4]?|c\d+)_r1\.htm$',
 'ottolenghi-yotam-ottolenghi': r'OEBPS/Otto_9781607744191_epub_(?:itr|fm1|c\d+)_r1\.htm$',
 'ottolenghi-flavor-a-cookbook-yotam-ottolenghi': r'OEBPS/xhtml/(?:intro|fm2|c\d+[a-z]?(?:_[a-z]+)?|bm[12])\.xhtml$',
 'kismet-bright-fresh-vegetable-loving-recipes-sara-kramer': r'OEBPS/xhtml/(?:intro|c\d+)\.xhtml$',
 'nopalito-a-mexican-kitchen-gonzalo-guzman-stacy-adimando': r'OEBPS/(?:fm[12]|c\d+)\.xhtml$',
 'oaxaca-bricia-lopez-javier-cabral': r'OEBPS/xhtml/(?:intro|ch\d+)\.xhtml$',
 'what-goes-with-what-100-recipes-julia-turshen': r'OEBPS/xhtml/(?:introduction|fm2|part[1-6]|chapter(?:[1-9]|1[0-9]|2[0-8]))\.xhtml$',
 'my-little-french-kitchen-rachel-khoo': r'OPS/xhtml/\d+-(?:introduction|chapter\d+)\.html$',
 'i-dream-of-dinner-so-you-dont-have-to-ali-slagle': r'OEBPS/xhtml/(?:intro|c\d+[a-z]?|bm[12])\.xhtml$',
 'eat-up-new-zealand-al-brown': r'OEBPS/xhtml/(?:intro_[12]|ch\d+(?:_rec\d+)?)\.xhtml$',
})
for _book_id, _profile in BATCH_04_PROFILES.items():
    PROFILES[_book_id] = dict(_profile, version='epub-batch-4', extra_block_tags=('figcaption',), restrict_members=True, inline_page_ids=True)
PROFILES['ottolenghi-yotam-ottolenghi']['source_warnings'] = {
    title: 'Source ingredient list appears exchanged with the other focaccia topping; retained as supporting text pending source correction.'
    for title in ('Parsley and olive topping', 'Red onion and goat cheese topping')
}
PROFILES['ottolenghi-yotam-ottolenghi']['title_instruction_classes'] = {
    title: 'headnote' for title in ('Arugula and horseradish sauce', 'Watercress and mustard sauce', 'Grape and fennel seed topping', 'Fresh berries', 'Vanilla extract')
}
PROFILES['nopalito-a-mexican-kitchen-gonzalo-guzman-stacy-adimando'].update(
    bibliography=dict(title='Nopalito: A Mexican Kitchen', author='Gonzalo Guzmán; Stacy Adimando'),
    bibliography_evidence='OEBPS/copy.xhtml: cataloguing title credits Gonzalo González Guzmán, with Stacy Adimando; OEBPS/ata.xhtml identifies both authors.')
PROFILES['oaxaca-bricia-lopez-javier-cabral'].update(
    bibliography=dict(title='Oaxaca: Home Cooking from the Heart of Mexico', author='Bricia Lopez; Javier Cabral'),
    bibliography_evidence='OEBPS/xhtml/authors.xhtml: ABOUT THE AUTHORS identifies Bricia Lopez and Javier Cabral.')
for _book_id, _isbn, _title in [('jerusalem-yotam-ottolenghi', '1607743958', 'Jerusalem'), ('ottolenghi-yotam-ottolenghi', '1607744191', 'Ottolenghi: The Cookbook')]:
    PROFILES[_book_id].update(bibliography=dict(title=_title, author='Yotam Ottolenghi; Sami Tamimi'), bibliography_evidence=f'OEBPS/Otto_978{_isbn}_epub_cop_r1.htm: text copyright credits Yotam Ottolenghi and Sami Tamimi.')

CONTENT_MEMBERS['plenty-yotam-ottolenghi'] = r'OPS/\d+-(?:introduction|chapter\d+)\.html$'
PROFILES['plenty-yotam-ottolenghi'] = dict(
    titles='left', ingredients='hang', instructions='', yields='', yield_tags=('h4',),
    groups='', headnotes='', barriers='', barrier_tags=('h1',), shared_prose='noindent',
    shared_prose_after_ingredients=True, restrict_members=True, inline_page_ids=True,
    extra_block_tags=('figcaption',), version='epub-batch-5', markup_fractions=True, nav_members=('OPS/003-toc.html',),
    role_patterns=(('hang', r'^(?:Mayonnaise|Poaching liquor|Relish|Basil oil|Dipping sauce|Broth|Dumplings|Sauce|Harissa|Filling|Parsley oil|Black bean paste|Béchamel|Croutons|Dressing|Eggplant sauce|Flatbreads|Ful|Herb paste|Lime butter|Marinade|Mushroom Ragout|Oven-dried tomatoes|Pasta dough|Poached peaches|Polenta|Rice|Salsa|Sambal|Satay sauce|Spice paste|Spiced butter|Tarragon aioli \(optional\))$', 'ingredient_group'),))

# Batch 6: source-inspected layouts; French Cooking editions remain deferred.
BATCH_06_PROFILES = {
 'an-invitation-to-indian-cooking-a-cookbook-madhur-jaffrey': dict(titles='rt-supb-pg rt rt-alt1 rt-alt2', join_title_blocks=True, ingredients='ril rilf rill ril-alt1 rilf-alt1', instructions='rpf rp rpl rps rph rph-alt1 rph-alt2', yields='ry ry-alt1 ry-alt2', groups='rilh rilh-alt1', headnotes='rhnf rhn rhn-alt1 rhnf-alt1', barriers='h1 h1-alt h3aft h3-supb-pg fmt-alt-in-pg'),
 'bravetart-iconic-american-desserts-stella-parks': dict(titles='h3s h3ss', ingredients='ing ing-spa', instructions='stps stpst sidh', yields='yld', groups='ingh', headnotes='descri descri1', notes='mixh makes', barriers='h3n h3p h3s1 h3np h2c h2cc h2p h2pp h2a h'),
 'essentials-of-classic-italian-cooking-new-ed-marcella-hazan': dict(titles='rt rtaft', ingredients='ril rilf rill rils', instructions='rp rps rpf rp-alt rpf-alt', yields='ry ry-alt', groups='rilh', headnotes='rhnf rhn rhnl rhns rhn-alt rhnf-alt', notes='rvh', barriers='ct-chap-pg h1 h1-alt h1aft h2 h2aft h3 menuh menuh1 menuh1_aft', barrier_exceptions=('Suggested Cheese Combinations', 'Cooking the Pasta', 'Boiling 1 pound of green beans')),
 'honey-n-co-the-cookbook-itamar-srulovich': dict(titles='recipe-title', ingredients='ingredient ingredient1', instructions='', yields='yield', groups='ingredients-title ingredients-title1', headnotes='', shared_prose='space-break noindent space-break1 space-break2 ul-list', shared_prose_after_ingredients=True, barriers='sect sect1'),
 'ideas-in-food-aki-kamozawa': dict(titles='', title_tags='h3', ingredients='bl_hanging', instructions='', yields='bl_nonindent', groups='', headnotes='', shared_prose='nonindent indent', shared_prose_after_ingredients=True, barriers='chapter part chapter2', barrier_tags=('h2',)),
 'la-son-roy-choi-tien-nguyen-natasha-phan': dict(titles='h3 h3a', ingredients='tip tip1', instructions='noindenta noindentat noindentv2 noindentv3 noindents1 noindentv1 noindentv4', yields='right rightd rightka', groups='head head2 head1', headnotes='noindentm noindentma noindentm2 noindentm1 noindenth', shared_prose='noindent noindentax noindentax1', shared_prose_after_ingredients=True, barriers='h1 h1b h1bs h2k', role_patterns=(('heada headsa', '.*', 'ingredient_group'), ('noindent', '^For blueberry pancakes,', 'note'))),
 'meathead-rux-martin': dict(titles='RT', ingredients='ING', instructions='STEPS STEPS-follow STEPS-HED', yields='Y', join_yields=True, groups='ING-H', headnotes='HEDNOTE', barriers='Heading-1 Heading-2 Heading-3 Heading-4 MYTH-HEADS'),
 'ratio-michael-ruhlman': dict(titles='extractHead', ingredients='extractVerse', instructions='', shared_prose='extractText extractTextNoIndent', shared_prose_after_ingredients=True, yields='paraCenter', groups='extractVerse1', headnotes='', barriers='chapterTitle chapterHeadA chapterHeadB partNumber'),
 'salt-to-taste-marco-canora': dict(titles='recipe-title recipe-titleg recipe-titleg1', ingredients='r-ingred r-ingred1', instructions='r-direct para1', yields='r-serve', groups='r-ingred-h', headnotes='r-desc r-desc1 r-desc2 r-desc2a', barriers='chapter-head box-title fm-title', notes='r-var'),
 'tender-nigel-slater': dict(titles='Header_RecipeTitle', ingredients='hanging', instructions='', yields='nonindent1', groups='ingredient_head', headnotes='', shared_prose='nonindent indent', shared_prose_after_ingredients=True, barriers='Header_StyleA Header_StyleB Header_StyleB1 Header_StyleC Header_StyleD section'),
 'the-curry-guy-bible-dan-toombs': dict(titles='rep-ttl', ingredients='hang', instructions='tx2 tx', yields='h3a', groups='', headnotes='txm', barriers='h1 cn ct', barrier_exceptions=('(PRAWN WITH PINEAPPLE)', '(INDIAN FIVE SPICE)', '(INDIAN SCOTCH EGG)'), role_patterns=(('h3 h3b', r'^(?:PREP TIME|COOKING TIME)', 'note'),)),
 'the-flavor-equation-nik-sharma': dict(titles='section_title', ingredients='ingred_sublist', instructions='method_txt', yields='yield', groups='', headnotes='', shared_prose='first1_noindent first1_indent', shared_prose_after_ingredients=True, barriers='section_hd chapter_title chapter_hd', role_patterns=(('first1_noindent', r'^For (?:the .*|serving|garnish):$', 'ingredient_group'), ('first1_noindent', r'^VARIATION:', 'note'))),
 'the-food-of-sichuan-fuchsia-dunlop': dict(titles='recipes_rh recipe-name-englisht recipe-name-english-h recipe-name-english', ingredients='recipes_ing1 recipes_ing', instructions='recipes_recipe-tx recipes_recipe-tx-ital recipes_recipe-tx-ital1 recipes_tx-lists_recipe-tx2n recipes_tx-lists_recipe-tx2t recipes_tx-lists_recipe-tx2l', yields='recipes_hy recipes_hy-l', groups='', headnotes='recipes_rh1 noindenth noindenth1', shared_prose='recipes_sidetxfl tx', method_start='recipes_recipe-tx', barriers='h1 h1-v h2 h2-v h3 h', role_patterns=(('recipes_recipe-tx-ul recipes_recipe-tx-ul1',r'^[Ff]or (?:the |a |each )','ingredient_group'),)),
 'the-whole-fish-cookbook-osh-nil': dict(titles='rep-ttl', ingredients='hang hang1', instructions='', yields='h3', groups='h3a h3a1', headnotes='', shared_prose='tx2 tx3 tx6 tx7 tx5', shared_prose_after_ingredients=True, barriers='ttl h1 h2 chap', notes='tx4'),
 'think-like-a-chef-tom-colicchio': dict(titles='recipe_title recipe_title1 subrecipe_title', ingredients='IL_text IL_text1', instructions='method', yields='yield', groups='IL_head IL_head1', headnotes='headnote headnote1', barriers='A_head A_head1 B_head essay_head introduction textbox_head'),
}
CONTENT_MEMBERS.update({
 'an-invitation-to-indian-cooking-a-cookbook-madhur-jaffrey': r'OEBPS/xhtml/(?:fm[12]|intro1?|c\d+|bm1)\.xhtml$',
 'bravetart-iconic-american-desserts-stella-parks': r'text/part00(?:0[7-9]|1\d|2[0-4])(?:_split_\d+)?\.html$',
 'essentials-of-classic-italian-cooking-new-ed-marcella-hazan': r'OEBPS/xhtml/(?:fm[1-3]|intro|c\d+)\.xhtml$',
 'honey-n-co-the-cookbook-itamar-srulovich': r'text/part0(?:00[4-9]|0[1-9]\d|1[0-5]\d|16[0-4]|170)(?:_split_\d+)?\.html$',
 'ideas-in-food-aki-kamozawa': r'OEBPS/Text/Kamo_9780307719744_epub_(?:itr|p\d+|c\d+)_r1\.htm$',
 'la-son-roy-choi-tien-nguyen-natasha-phan': r'OEBPS/text/9780062202642_(?:Introduction|Chapter_\d+(?:-sec\d+)?|Back_Matter)\.xhtml$',
 'meathead-rux-martin': r'OEBPS/text/(?:fm1[3-6]|p(?!365|367|368)\d+[a-z]?)\.xhtml$',
 'ratio-michael-ruhlman': r'OEBPS/(?:9781416566120_(?:fm\d+|pt\d+|ch\d+|c\d+)|footnote\d+)\.html$',
 'salt-to-taste-marco-canora': r'OEBPS/(?:FM_(?:FOREWORD|INTRODUCTION)|\d+-\d+_CH\d+(?:_REC\d+)?)_9781605293219\.xhtml$',
 'tender-nigel-slater': r'OEBPS/Slat_9781607740407_epub_(?:itr|c\d+|bm1)_r1\.htm$',
 'the-curry-guy-bible-dan-toombs': r'OEBPS/chapter(?:0[1-9]|1[0-3])\.xhtml$',
 'the-flavor-equation-nik-sharma': r'OPS/xhtml/\d+-(?:frontmatter0[12]|foreword|introduction|part\d+|chapter(?:00[1-9]|0[1-9]\d|1[01]\d|12[0-4]))\.xhtml$',
 'the-food-of-sichuan-fuchsia-dunlop': r'OEBPS/Text/(?:05_Introduction|\d+_Chapter(?:0[1-9]|1\d|20|23)(?:_split_\d+)?)\.xhtml$',
 'the-whole-fish-cookbook-osh-nil': r'OEBPS/9781743586631_Split(?:[6-9]|[1-9]\d|100|101)\.html$',
 'think-like-a-chef-tom-colicchio': r'OEBPS/Coli_9780770433895_epub_(?:frw|prf|itr|p\d+|c\d+|bm1)_r1\.htm$',
})
BATCH_06_PROFILES['the-food-of-sichuan-fuchsia-dunlop'].update(
    title_exceptions=(('recipes_rh1', 'Salt-fried Pork'), ('recipes_rh1', 'Spicy Blood Stew'), ('noindenth', 'Duck Braised with Konnyaku “Tofu”'), ('noindenth', 'Dry-fried Eggplants'), ('noindenth', 'Stir-fried Potato Slivers with Chiles and Sichuan Pepper')),
    title_instruction_classes={'Gong Bao Chicken with Peanuts': 'noindenth'})
BATCH_06_PROFILES['the-food-of-sichuan-fuchsia-dunlop']['role_patterns'] += (
    ('recipes_ing recipes_ing1', r'^(?:For the (?:marinade|sauce|pre-cooking|seasonings)|To serve)$', 'ingredient_group'),
    ('recipes_ing recipes_ing1', r'^(?:The City of Brocade \[Chengdu\] may be a pleasant place,|But it is best to seek your home\.|For it is easier to climb to Heaven|Than to take the Sichuan road\.)$', 'note'))
BATCH_06_PROFILES['tender-nigel-slater']['role_patterns'] = (('hanging', r'^for the (?:pastry|gravy|dressing|eggplants|pork balls|fish|salad|marrow)$', 'ingredient_group'),)
BATCH_06_PROFILES['honey-n-co-the-cookbook-itamar-srulovich']['role_patterns'] = (('ingredient', r'^For the dressing$', 'ingredient_group'),)
BATCH_06_PROFILES['the-flavor-equation-nik-sharma']['role_patterns'] += (('first1_noindent', r'^Sea salt flakes$', 'ingredient'),)
BATCH_06_PROFILES['ratio-michael-ruhlman'].update(
    role_patterns=(('extractText', r'^(?:1 ½ ounces butter \(3 tablespoons\)|1 ounce flour \(3 tablespoons\))$', 'ingredient'), ('extractTextNoIndent', r'^(?:8 ounces eggs \(4 large eggs|1 tablespoon butter kneaded with 1 tablespoon flour)', 'ingredient'),),
    notes_patterns=(('paraCenter', r'^Quatre Épices$'), ('extractVerse1', r'^For (?:White|Brown) Veal Stock$')))
BATCH_06_PROFILES['the-food-of-sichuan-fuchsia-dunlop']['notes_patterns_after_method'] = True
BATCH_06_PROFILES['the-food-of-sichuan-fuchsia-dunlop']['notes_patterns'] = (('recipes_recipe-tx-ul recipes_recipe-tx-ul1', r'^(?![Ff]or |To serve|To finish)'),)
BATCH_06_PROFILES['think-like-a-chef-tom-colicchio']['title_ingredient_classes'] = {'spiced roasted lobster with pea ravioli': 'indent'}
BATCH_06_PROFILES['meathead-rux-martin']['equipment_before_ingredient'] = {'Whole Hog Pig Pickin’': '1 (75-pound) hog (see below)'}
for _book_id, _profile in BATCH_06_PROFILES.items():
    PROFILES[_book_id] = dict(_profile, version='epub-batch-6', extra_block_tags=('figcaption',), restrict_members=True, inline_page_ids=True)
PROFILES['ideas-in-food-aki-kamozawa'].update(bibliography=dict(title='Ideas in Food', author='Aki Kamozawa; H. Alexander Talbot'), bibliography_evidence='OEBPS/Text/Kamo_9780307719744_epub_cop_r1.htm: text copyright and cataloguing credits both authors.')
PROFILES['la-son-roy-choi-tien-nguyen-natasha-phan'].update(bibliography=dict(title='L.A. Son', author='Roy Choi; Tien Nguyen; Natasha Phan'), bibliography_evidence='OEBPS/text/9780062202642_Copyright.xhtml and About_the_Author.xhtml credit all three authors.')
PROFILES['meathead-rux-martin'].update(bibliography=dict(title='Meathead: The Science of Great Barbecue and Grilling', author='Meathead Goldwyn; Greg Blonder'), bibliography_evidence='OEBPS/text/fm04.xhtml: Library of Congress cataloguing credits Meathead Goldwyn with Greg Blonder; Rux Martin is the imprint.')
PROFILES['the-whole-fish-cookbook-osh-nil'].update(bibliography=dict(title='The Whole Fish Cookbook', author='Josh Niland'), bibliography_evidence='OEBPS/9781743586631_Split102.html: text copyright Josh Niland 2019.')

from .epub_batch07 import PROFILES as BATCH_07_PROFILES, MEMBERS as BATCH_07_MEMBERS
CONTENT_MEMBERS.update(BATCH_07_MEMBERS)
for _book_id, _profile in BATCH_07_PROFILES.items():
    PROFILES[_book_id] = dict(_profile, version='epub-batch-7', extra_block_tags=('figcaption',), restrict_members=True, inline_page_ids=True)

from .epub_batch08 import PROFILES as BATCH_08_PROFILES, MEMBERS as BATCH_08_MEMBERS
PROFILES.update(BATCH_08_PROFILES)
CONTENT_MEMBERS.update(BATCH_08_MEMBERS)

PROFILES['med-claudia-roden'] = dict(version='epub-batch-med-1',
    titles='Ahead Ahead1', ingredients='ingred', instructions='',
    shared_prose='body_copy list1', shared_prose_after_ingredients=True,
    yields='serves', groups='BHead', headnotes='', barriers='',
    nav_classes='center_content', restrict_members=True,
    recipe_member_pattern=r'EPUB/xhtml/chapter\d+-02\.xhtml$')
CONTENT_MEMBERS['med-claudia-roden'] = r'EPUB/xhtml/(?:chapter\d+(?:-02)?|preface\d+)\.xhtml$'

from .epub_rustic_italian import BOOK_ID as RUSTIC_ID, MEMBERS as RUSTIC_MEMBERS, PROFILE as RUSTIC_PROFILE
PROFILES[RUSTIC_ID] = RUSTIC_PROFILE
CONTENT_MEMBERS[RUSTIC_ID] = RUSTIC_MEMBERS

BLOCK_TAGS = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'div', 'blockquote', 'table', 'img'}
CONTAINER_TAGS = BLOCK_TAGS - {'img'}


def source_blocks(member, doc, extra_tags=(), inline_page_ids=False, section_title_anchors=False, last_inline_page=False, fixed_layout_spans=False):
    """Walk publisher blocks once, including leaf divs and table/image evidence."""
    if fixed_layout_spans:
        from .epub_batch07 import fixed_layout_blocks
        return fixed_layout_blocks(member, doc)
    page = None
    result = []
    block_tags = BLOCK_TAGS | set(extra_tags)
    container_tags = block_tags - {'img'}
    for ordinal, n in enumerate(doc.iter()):
        if not isinstance(n.tag, str):
            continue
        anchor = n.get('id', '')
        if n.get('epub:type') == 'pagebreak' and re.fullmatch(r'pg\d+', anchor):
            page = anchor[2:]
        elif n.get('role') == 'doc-pagebreak':
            page = n.get('title') or n.get('aria-label') or page
        elif re.fullmatch(r'page_?\d+', anchor):
            page = re.search(r'\d+', anchor)[0]
        if n.tag not in block_tags or n.xpath('ancestor::table'):
            continue
        children = [c for c in n.iterdescendants() if c.tag in container_tags]
        fragment = None
        if n.tag != 'table' and children:
            # Some list items contain a method sentence followed by a nested
            # note paragraph. Emit that leading sentence before the note.
            leading = [n.text or '']
            for child in n:
                if child.tag in container_tags or any(c.tag in container_tags for c in child.iterdescendants()):
                    break
                if isinstance(child.tag,str):leading.append(child.text_content())
                leading.append(child.tail or '')
            fragment=clean(''.join(leading))
            if not fragment:continue
        text = fragment if fragment is not None else clean(n.text_content())
        if not text and n.tag != 'img':
            continue
        if is_source_artifact(text) or n.get('class') == 'footnote_skip':
            continue
        ids = ([anchor] if anchor else []) + n.xpath('.//*[@id]/@id')
        if section_title_anchors:
            # This edition links its contents to a section whose explicit title
            # matches the recipe heading. Never give that anchor to subrecipes.
            ids = [a.get('id') for a in n.iterancestors()
                   if a.tag == 'section' and a.get('id') and clean(a.get('title','')) == text] + ids
        links = []
        for a in n.xpath('.//a[@href]'):
            href = a.get('href'); target = resolve(member, href)
            links.append(dict(text=clean(a.text_content()), path=target, anchor=unquote(urlsplit(href).fragment), href=href))
        local_pages = n.xpath('.//*[@role="doc-pagebreak"]/@title')
        local_pages += [x.get('id')[2:] for x in n.iterdescendants()
                        if x.get('epub:type') == 'pagebreak' and re.fullmatch(r'pg\d+', x.get('id', ''))]
        if inline_page_ids:
            markers=[x for x in n.iter() if isinstance(x.tag,str) and (x.get('role')=='doc-pagebreak' or re.fullmatch(r'page_?\d+',x.get('id','')))]
            local_pages=[x.get('title') or x.get('aria-label') or re.search(r'\d+',x.get('id',''))[0] for x in markers]
        block_page = local_pages[-1] if last_inline_page and local_pages else next(iter(local_pages), page)
        b = dict(text=text, tag=n.tag, classes=n.get('class','').split(), path=member,
                 anchors=ids, pages=[block_page] if block_page else [], links=links,
                 element_index=ordinal, xpath=doc.getroottree().getpath(n),
                 ancestor_classes=[c for a in n.iterancestors() for c in a.get('class','').split()])
        if fragment is not None:b['source_fragment']='leading inline text before nested blocks'
        if n.tag == 'img':
            b.update(text=n.get('alt',''), assets=[resolve(member,n.get('src',''))], kind='image')
        if n.tag == 'table':
            b['rows']=[[clean(c.text_content()) for c in row.xpath('./td|./th')] for row in n.xpath('.//tr')]
            b['text']='\n'.join(' | '.join(row) for row in b['rows']); b['kind']='table'
        result.append(b)
    return result


def matches(b, words):
    return bool(set(b['classes']) & set(words.split()))


def is_barrier(block, profile):
    if profile.get('separate_leading_artwork') and block.get('source_boundary'):
        return True
    if block['text'] in profile.get('barrier_exceptions', ()): return False
    return block['tag'] in profile.get('barrier_tags', ()) or matches(block,profile['barriers']) or (block['tag']=='img' and block['text'] in profile.get('barrier_image_alts',()))


def selected_epub(book, source_dir):
    """Use a resolved edition only while every reviewed source hash matches."""
    files = [f for f in book['files'] if f['format'] == 'epub']
    review = book.get('edition_review', {})
    if review.get('status') == 'resolved':
        reviewed = {f['name']: f['sha256'] for f in review['files']}
        if set(reviewed) != {f['name'] for f in files}:
            raise ValueError('Edition review file set changed: ' + book['id'])
        for name, digest in reviewed.items():
            if hashlib.sha256((source_dir / name).read_bytes()).hexdigest() != digest:
                raise ValueError('Edition review source changed: ' + name)
        return source_dir / review['preferred_epub']
    if len(files) != 1:
        raise ValueError('Edition selection required: ' + book['title'])
    return source_dir / files[0]['name']


def extract(book, source_dir, *, profile=None, member_pattern=None):
    """Extract a registered edition or an explicitly supplied layout profile.

    Custom profiles must supply a substantive member pattern; no private
    catalog or process-global registry mutation is needed by external callers.
    """
    if profile is None:
        profile = PROFILES[book['id']]
        member_pattern = member_pattern or CONTENT_MEMBERS[book['id']]
    elif not member_pattern:
        raise ValueError('A custom EPUB profile requires an explicit member_pattern')
    original_bibliography = {k:book[k] for k in ('title','author')}
    book = {**book, **profile.get('bibliography', {})}
    version = profile.get('version', VERSION)
    path=selected_epub(book, source_dir); digest=hashlib.sha256(path.read_bytes()).hexdigest()
    documents=list(read_epub(path))
    stream=[]; nav=[]
    for member,doc in documents:
        blocks = source_blocks(member,doc,profile.get('extra_block_tags',()),profile.get('inline_page_ids',False),profile.get('section_title_anchors',False),profile.get('last_inline_page',False),profile.get('fixed_layout_spans',False))
        if profile.get('inherited_source_anchors'):
            from .epub_rustic_italian import retain_container_anchors
            retain_container_anchors(blocks, doc)
        if profile.get('separate_leading_artwork'):
            from .epub_rustic_italian import mark_leading_artwork
            mark_leading_artwork(blocks)
        if profile.get('bold_paragraph_titles'):
            for block in blocks:
                if block['tag'] != 'p': continue
                node = doc.getroottree().xpath(block['xpath'])[0]
                if 'center' in block['classes'] and any(clean(n.text_content()) == block['text'] for n in node.xpath('./strong|./b')):
                    block['derived_role'] = 'title'
        if profile.get('markup_fractions'):
            for block in blocks:
                node = copy.deepcopy(doc.getroottree().xpath(block['xpath'])[0])
                for numerator in node.xpath('.//sup[@class="frac"]'):
                    previous = numerator.getprevious()
                    owner, field = (previous, 'tail') if previous is not None else (numerator.getparent(), 'text')
                    before = getattr(owner, field) or ''
                    if re.search(r'\d$', before): setattr(owner, field, before + ' ')
                normalized = clean(node.text_content())
                if normalized != block['text'] and block.get('kind') not in {'image','table'}:
                    block['markup_fraction_text'] = normalized
        stream.extend(blocks)
        for a in doc.xpath('//a[@href]'):
            if member in profile.get('nav_members', ()) or any('toc' in c.lower() or 'recipe_list' in c.lower() or c in {'pt-text','pt-text1'} | set(profile.get('nav_classes','').split()) for n in [a,*a.iterancestors()] for c in n.get('class','').split()):
                nav.append(dict(label=clean(a.text_content()),path=resolve(member,a.get('href')),anchor=unquote(urlsplit(a.get('href')).fragment)))
    recipes=[]; rejected=[]; segments=[]; current=None
    for b in stream:
        if profile.get('restrict_members') and not re.fullmatch(member_pattern,b['path']):
            if current:segments.append(current);current=None
            continue
        title = matches(b,profile['titles']) or b['tag'] in profile.get('title_tags','').split()
        title = title or b.get('derived_role') == 'title'
        title = title or any(matches(b, classes) and b['text'] == text for classes, text in profile.get('title_exceptions', ()))
        if profile.get('non_title_pattern') and re.search(profile['non_title_pattern'], b['text']): title = False
        if profile.get('recipe_member_pattern') and not re.fullmatch(profile['recipe_member_pattern'], b['path']): title = False
        barrier = is_barrier(b,profile)
        if current and b['text'] in profile.get('title_barrier_exceptions', {}).get(current[0]['text'], ()):
            barrier = False
        # A new XHTML file is a boundary, except explicit Calibre split siblings.
        if current and b['path'] != current[0]['path']:
            previous=re.sub(r'_split_\d+','',current[0]['path'])
            following=re.sub(r'_split_\d+','',b['path'])
            if previous != following or '_split_' not in b['path']:
                segments.append(current);current=None
        if title and profile.get('join_title_blocks') and current and all(matches(x,profile['titles']) for x in current):
            current.append(copy.deepcopy(b));continue
        if title or barrier:
            if current:segments.append(current);current=None
            if title:current=[copy.deepcopy(b)]
        elif current is not None:
            current.append(copy.deepcopy(b))
    if current:segments.append(current)
    for section in segments:
        head=section[0]; head['kind']='title'; phase='intro'; normalizations=[]
        title_parts=[head['text']]
        for i,b in enumerate(section[1:],1):
            if b.get('kind') == 'table' and matches(b, profile.get('ingredient_tables', '')):
                # Retain the original table and cell boundaries. This derived
                # view joins quantity/name cells in their explicit source order.
                b['ingredient_lines'] = [' '.join(c for c in row if c) for row in b['rows'] if any(row)]
                continue
            if b.get('kind') in {'image','table'}:continue
            if profile.get('join_title_blocks') and matches(b,profile['titles']):
                b['kind']='title';title_parts.append(b['text']);continue
            if matches(b,profile.get('notes','')) or ((not profile.get('notes_patterns_after_method') or any(x.get('kind') == 'instruction' for x in section[:i])) and any(matches(b, classes) and re.search(pattern, b['text']) for classes, pattern in profile.get('notes_patterns', ()))):phase='notes'
            if (profile.get('shared_prose_after_ingredients') and phase=='intro'
                    and matches(b,profile.get('shared_prose',''))
                    and any(x.get('kind')=='ingredient' for x in section[:i])):
                phase='instructions'
            if matches(b,profile.get('method_separator','')) and phase!='notes' and any(x.get('kind')=='ingredient' for x in section[:i]):phase='instructions'
            if matches(b,profile.get('method_start','')) and phase!='notes':phase='instructions'
            if matches(b,profile['ingredients']) or (b['tag']=='li' and set(b['ancestor_classes']) & set(profile.get('ingredient_ancestors','').split())):kind='ingredient' if phase!='notes' else 'supplement_ingredient'
            elif set(b['ancestor_classes']) & set(profile.get('headnote_ancestors','').split()):kind='headnote'
            elif matches(b,profile.get('shared_prose','')):kind={'intro':'headnote','instructions':'instruction','notes':'note'}[phase]
            elif matches(b,profile['instructions']):kind='instruction' if phase!='notes' else 'note';phase='instructions' if phase!='notes' else phase
            elif matches(b,profile['yields']) or b['tag'] in profile.get('yield_tags', ()):kind='yield'
            elif matches(b,profile['groups']):kind='ingredient_group'
            elif matches(b,profile['headnotes']) or (book['id']=='all-about-roasting-molly-stevens' and phase=='intro' and matches(b,'noindent1 indent')):kind='headnote'
            else:kind='note'
            if matches(b, profile.get('title_instruction_classes', {}).get(head['text'], '')):
                kind = 'instruction'
            for classes, pattern, override in profile.get('role_patterns', ()):
                if matches(b, classes) and re.search(pattern, b['text']):
                    kind = override
                    if kind == 'instruction': phase = 'instructions'
                    break
            if matches(b, profile.get('title_ingredient_classes', {}).get(head['text'], '')):
                kind = 'ingredient'
            if b.get('derived_role') in {'ingredient', 'instruction', 'headnote', 'note'}:
                kind = b['derived_role']
            if (profile.get('instruction_before_first_ingredient') and kind == 'headnote'
                    and b['text'].endswith(':') and i + 1 < len(section)
                    and matches(section[i + 1], profile['ingredients'])):
                kind = 'instruction'; phase = 'instructions'
            equipment_end = profile.get('equipment_before_ingredient', {}).get(head['text'])
            if kind == 'ingredient' and equipment_end and b['text'] != equipment_end and not any(x['text'] == equipment_end for x in section[:i]):
                kind = 'equipment'
            if kind == 'ingredient' and profile.get('equipment_pattern') and re.search(profile['equipment_pattern'], b['text'], re.I):
                kind = 'equipment'
            b['kind']=kind
            markup_text = b.pop('markup_fraction_text', b['text'])
            text,changes=normalize_fractions(markup_text)
            if markup_text != b['text']:
                changes.insert(0, dict(original=b['text'], normalized=markup_text, rule='explicit superscript fraction separated from preceding whole number'))
            if changes:
                b['source_text']=b['text'];b['text']=text
                normalizations.extend({**c,'block_index':i} for c in changes)
        ingredients=[line for b in section for line in ([b['text']] if b['kind']=='ingredient' else b.get('ingredient_lines', []))]
        instructions=[b['text'] for b in section if b['kind']=='instruction']
        warnings=[]
        if profile.get('dependency_ingredient_pattern') and any(re.search(profile['dependency_ingredient_pattern'], x) for x in ingredients):
            warnings.append('Ingredient list depends on a master recipe; retained as supporting text.')
        if head['text'] in profile.get('source_warnings', {}):
            warnings.append(profile['source_warnings'][head['text']])
        if not ingredients:warnings.append('No separate primary ingredient list identified.')
        if not instructions:warnings.append('No separate main method identified.')
        if any('\ufffd' in b['text'] or '\x00' in b['text'] for b in section):warnings.append('Replacement/control character in source.')
        anchor=next(iter(head['anchors']),'')
        rid=hashlib.sha256((book['id']+'|'+head['path']+'|'+anchor+'|'+head['text']).encode()).hexdigest()[:16]
        record=dict(id=rid,book_id=book['id'],book=book['title'],author=book['author'],title=' '.join(title_parts),ingredients=ingredients,instructions=instructions,
                    headnote='\n'.join(b['text'] for b in section if b['kind']=='headnote'),
                    yield_=next((b['text'] for b in section if b['kind']=='yield'),None),blocks=section,
                    source_text='\n'.join(b.get('source_text',b['text']) for b in section),warnings=warnings,normalizations=normalizations,
                    source=dict(file=str(path.resolve()),sha256=digest,member=head['path'],anchor=anchor,printed_page=next(iter(head['pages']),None),extractor=version),
                    references=[],supporting_notes=[],extraction_status='candidate')
        record['yield']=record.pop('yield_')
        if profile.get('yield_tables'):
            record['yield'] = next((b['text'] for b in section if b['kind']=='table' and matches(b, profile['yield_tables'])), record['yield'])
        if profile.get('join_yields'):
            record['yield']=' '.join(b['text'] for b in section if b['kind']=='yield') or None
        # Explicit source qualifications do not negate structural completeness.
        # Keep them visible while preserving the older fatal-warning behavior.
        collection = rejected if warnings else recipes
        qualifications = profile.get('qualification_warnings', {}).get(head['text'], [])
        if qualifications:
            record['warnings'].extend(qualifications)
            record['source_qualifications'] = list(qualifications)
        collection.append(record)
    lookup={(b['path'],a):r['id'] for r in recipes for b in r['blocks'] for a in b['anchors']}
    by_member={}
    for r in recipes:by_member.setdefault(r['source']['member'],[]).append(r['id'])
    unresolved=[]; matched=[]
    for entry in { (x['path'],x['anchor'],x['label']):x for x in nav }.values():
        target=lookup.get((entry['path'],entry['anchor']))
        if not target and not entry['anchor'] and len(by_member.get(entry['path'],[]))==1:
            target=by_member[entry['path']][0]
        (matched if target else unresolved).append({**entry,'recipe_id':target})
    report=dict(book_id=book['id'],title=book['title'],source_sha256=digest,extractor=version,
                candidates=len(segments),structured_candidates=len(recipes),held_for_review=len(rejected),
                held=[dict(id=r['id'],title=r['title'],warnings=r['warnings']) for r in rejected],
                toc_matched=matched,toc_unresolved=unresolved,
                source_blocks=len(stream),recipe_blocks=sum(len(r['blocks']) for r in recipes),
                classification_counts=dict(Counter(b['kind'] for r in recipes for b in r['blocks'])),
                source_review='Pending; structural completeness is not source-fidelity approval.')
    if profile.get('knowledge_only_reason'):
        report['knowledge_only_reason'] = profile['knowledge_only_reason']
    if profile.get('bibliography'):
        report['bibliographic_correction'] = dict(original=original_bibliography, corrected={k:book[k] for k in ('title','author')}, evidence=profile.get('bibliography_evidence','OEBPS/xhtml/copy.xhtml: text copyright Sami Tamimi and Tara Wrigley; Yotam Ottolenghi credited separately for the foreword.'))
    # Preserve surrounding technique, unstructured recipes and sidebars as
    # source passages. Never manufacture ingredient lists from their prose.
    used={(b['path'],b['element_index']) for r in recipes for b in r['blocks']}
    content_members={b['path'] for r in recipes+rejected for b in r['blocks']}
    content_members.update(member for member,_ in documents if re.fullmatch(member_pattern,member))
    groups=[];group=[]
    for b in stream:
        eligible=b['path'] in content_members and (b['path'],b['element_index']) not in used
        navigation=any('toc' in c.lower() or 'recipe_list' in c.lower() or c in {'pt-text','pt-text1'} | set(profile.get('nav_classes','').split()) for c in b['classes']+b['ancestor_classes'])
        heading=bool(re.fullmatch(r'h[1-4]',b['tag'])) or matches(b,profile['titles']) or b.get('derived_role') == 'title' or is_barrier(b,profile)
        if not eligible or navigation or (group and (b['path']!=group[0]['path'] or heading)):
            if group:groups.append(group);group=[]
        if eligible and not navigation:group.append(b)
    if group:groups.append(group)
    extra=[]
    for group in groups:
        text=next((b['text'] for b in group if b.get('kind')!='image' and b['text']),None)
        if not text:continue
        title=text if len(text)<=200 else 'Supporting text — '+Path(group[0]['path']).name
        if profile.get('fixed_layout_spans'):
            title = next((b['text'] for b in group if 'fs19' in b['classes']), 'Source page — '+Path(group[0]['path']).name)
        if group[0]['tag']=='img' and group[0]['text'] in profile.get('barrier_image_alts',()):title=group[0]['text']
        source=dict(book_id=book['id'],book=book['title'],author=book['author'],file=path.name,format='epub',sha256=digest,extractor=version)
        blocks=[]
        for b in group:
            loc={**source,'member':b['path'],'anchor':next(iter(b['anchors']),None),'element_index':b['element_index'],'printed_page':next(iter(b['pages']),None),'pdf_page':None}
            blocks.append({**b,'kind':b.get('kind','text'),'source':loc})
        sid='s_'+hashlib.sha256((book['id']+'|'+group[0]['path']+'|'+str(group[0]['element_index'])).encode()).hexdigest()[:16]
        extra.append(dict(id=sid,title=title,headings=[book['title'],title],source=blocks[0]['source'],blocks=blocks,flags=['Supporting source text; not a verified structured recipe.','Images retained by source reference; visual content is not transcribed.']))
        if profile.get('knowledge_only_reason'):
            extra[-1]['flags'].append(profile['knowledge_only_reason'])
    report['supporting_sections']=extra
    report['substantive_members']=sorted(content_members)
    report['excluded_members']=[member for member,_ in documents if member not in content_members]
    return recipes,rejected,report


def link_records(recipes, sections=()):
    anchors={(r['book_id'],b['path'],a):r['id'] for r in recipes for b in r['blocks'] for a in b['anchors']}
    members={}
    supporting={(s['source']['book_id'],b['source']['member'],a):s for s in sections for b in s['blocks'] for a in b.get('anchors',[])}
    for r in recipes:members.setdefault((r['book_id'],r['source']['member']),[]).append(r['id'])
    for r in recipes:
        if not r.get('source',{}).get('extractor','').startswith(('epub-batch-','epub-assembly-')):continue
        refs=[]
        for b in r['blocks']:
            for ref in b['links']:
                key=(r['book_id'],ref['path']);target=anchors.get((*key,ref['anchor']))
                if not target and not ref['anchor'] and len(members.get(key,[]))==1:target=members[key][0]
                note=supporting.get((*key,ref['anchor'])) if not target else None
                refs.append({**ref,'recipe_id':target,'supporting_note':note is not None})
                if note and not any(s.get('section_id')==note['id'] for s in r['supporting_notes']):
                    r['supporting_notes'].append(dict(book_id=r['book_id'],path=note['source']['member'],section_id=note['id'],title=note['title'],blocks=copy.deepcopy(note['blocks']),flags=note['flags']))
        r['references']=list({(r['path'],r['anchor'],r.get('href')):r for r in refs}.values())
