"""Source-reviewed publisher layouts for the final EPUB tranche.

Imported by epub_batch; no source text is bundled here.
"""
PROFILES = {
 'ad-hoc-at-home-thomas-keller': dict(titles='h2a3 h3bc', ingredients='noindenta noindentat', instructions='noindentt', shared_prose='indent', shared_prose_after_ingredients=True, yields='photor', groups='noindenta1', headnotes='', barriers='h2a h2a1 h3b h3r h3bc1 h3c h1a h1c'),
 'cocktail-codex-alex-day': dict(titles='rt rt-appendix', ingredients='ril rilf rill', instructions='rp rpnl', yields='', groups='', headnotes='rhn rh1', barriers='h1 h2a h3 sbt'),
 'cooking-at-home-david-chang-priya-krishna': dict(titles='RT', ingredients='RIL-SMALL', instructions='RP', yields='RY', groups='', headnotes='', barriers='CT H4'),
 'every-grain-of-rice-simple-chinese-home-c-fuchsia-dunlop': dict(titles='calibre3', ingredients='calibre34 calibre35', instructions='', shared_prose='calibre16 calibre17 calibre18', shared_prose_after_ingredients=True, yields='', groups='', headnotes='', notes='calibre36', barriers='calibre33 calibre21'),
 'hot-thai-kitchen-pailin-chongchitnant': dict(titles='recipe_title subrecipe_title', ingredients='', ingredient_tables='ing tab1', instructions='method', yields='serve_nonindent', groups='', headnotes='recipe_box_nonindent recipe_box_extract', barriers='chapterc subchapterc section notebtitle_head tab1_head'),
 'jamies-dinners-jamie-oliver': dict(titles='rechead rechead_nbk', ingredients='', ingredient_ancestors='ingredient_items1 ingredient_items ingredient_items1_TS', instructions='method', yields='serves', groups='ingredient_header1_TS', headnotes='rec_intro rec_intro_TS', barriers='chaphead chapter_title'),
 'japanese-cooking-shizuo-tsuji': dict(titles='recipe_head', ingredients='Il_item Il_item_2 Il_item1', instructions='', shared_prose='left', shared_prose_after_ingredients=True, yields='yield', join_yields=True, groups='Il_head', headnotes='rec_subhead', notes='var1', barriers='A_head A_head_h A_headb1 A_headb1s A_head1 A_headb2 part_head section', nav_classes='mini_toc2', role_patterns=(('Il_item Il_item_2 Il_item1', r':$', 'ingredient_group'),)),
 'liquid-intelligence-dave-arnold': dict(titles='Recipe_rT', ingredients='Recipe_ing1', instructions='Recipe_stps', yields='Recipe_yld', groups='Recipe_rH11 Recipe_rH3 Recipe_rH2', headnotes='Recipe_rHn Recipe_rHnind', barriers='h1 h2 h3 H3V7 H3V4 H3V2 h2v4 H2seqV2 h23 sidebarhead Sidebar_sidH1'),
 'sift-the-elements-of-great-baking-nicola-lamb': dict(titles='rec_head', ingredients='', ingredient_tables='smalltable2', instructions='method1 method', yields='center_content6', groups='', headnotes='no_indent noindent_topspace', barriers='chapter_title chapter_title2 chap_h'),
 'the-art-of-simple-food-alice-waters': dict(titles='section', ingredients='hanging1', instructions='', shared_prose='nonindent noindent indent', shared_prose_after_ingredients=True, yields='', groups='', headnotes='', barriers='chapter part', notes_patterns=(('section1', '^VARIATIONS?$'),), role_patterns=(('nonindent', r'^(?:MAKES|SERVES)\b', 'yield'),)),
 'the-book-of-cocktail-ratios-michael-ruhlman': dict(titles='h3reca h3rec', ingredients='item itema itemb itemc itemd', instructions='rectxt rectxt1', yields='', groups='', headnotes='recintro recintroi subtxt', barriers='h2fm h2ch h1bm h4a h1fm h2fma h1pt h3a'),
 'the-food-lab-better-home-cooking-through-j-kenji-lopez-alt': dict(titles='recipe_rt recipe_rt1a recipe_rt1', ingredients='recipe_i recipe_i-spc', instructions='recipe_rsteps1 recipe_rsteps srecipe_rsteps1 srecipe_rsteps noindentt', join_title_blocks=True, yields='recipe_y', groups='recipe_ih recipe_ih1 recipe_ih-1m recipe_ih-1m1', headnotes='', barriers='c-head d-head q-head1 b-head a-head'),
 'the-kitchen-diaries-nigel-slater': dict(titles='subhead2', ingredients='recp_txt', instructions='', shared_prose='none3 normal noneleft', shared_prose_after_ingredients=True, yields='', groups='', headnotes='', barriers='subhead1 subhead3 subhead4 subhead5 chtitle', role_patterns=(('none3 normal noneleft', r'^(?:Serves|Makes|Enough for)\b', 'yield'),)),
 'the-meathead-method-meathead': dict(titles='repi_head', ingredients='repi_ing', instructions='repi_ins repi_ins1 repi_ins2 repi_para_ts1', yields='repi_y1 repi_y', join_yields=True, groups='', headnotes='repi_hn repi_hn_in', barriers='chap_ahead chap_bhead box_headm box_head1'),
 'the-zuni-cafe-cookbook-judy-rodgers': dict(titles='sub-head sub-right sub-cen sub-right-small', ingredients='hang', instructions='indentsp', yields='inge-head inge-head-1', groups='hang-ind', headnotes='noindent indent', barriers='chap-head sub-head-fp', role_patterns=(('indentsp', r'^NOTE\b', 'note'),)),
 'the-wok-recipes-and-techniques-j-kenji-lopez-alt': dict(titles='chap_hd', ingredients='ing-list ing-list5 ing-listty', instructions='numberg', shared_prose='noindent_para indent_para', method_start='dir', yields='', yield_tables='tableb', groups='ing-ts ing-hb ing-h ing-h1', headnotes='', barriers='chap-title ch1 ch2 ch2s h1 h2 h3 h2f h2b sbox-h1'),
}
# This class also appears inside mutually exclusive sausage seasonings. Limit
# restoration to the reviewed base recipes instead of merging every variant.
PROFILES['the-food-lab-better-home-cooking-through-j-kenji-lopez-alt']['title_ingredient_classes'] = {
    title: 'srecipe_i' for title in (
        'EASY GRILLED NAAN-STYLE FLATBREAD', 'EVEN EASIER GRILLED FLATBREAD',
        'ALL-AMERICAN MEAT LOAF', 'TENDER ITALIAN MEATBALLS WITH RICH TOMATO SAUCE')}
MEMBERS = {
 'ad-hoc-at-home-thomas-keller': r'text/part00(?:0[4-9]|1[0-6])(?:_split_\d+)?\.html$',
 'cocktail-codex-alex-day': r'OEBPS/(?:fm[12]|intro|c\d+[abc]|bm[123])\.xhtml$',
 'cooking-at-home-david-chang-priya-krishna': r'OEBPS/xhtml/(?:intro(?:_[a-f])?|c\d+|bm[12])\.xhtml$',
 'every-grain-of-rice-simple-chinese-home-c-fuchsia-dunlop': r'Every_Grain_of_Rice__Simple_Chi_split_(?:00[4-9]|0[1-9]\d|1[0-7]\d|18[0-2])\.html$',
 'hot-thai-kitchen-pailin-chongchitnant': r'OEBPS/Chon_9780449017067_epub_(?:itr\d?|c\d+|fm\d+)_r1\.htm$',
 'jamies-dinners-jamie-oliver': r'EPUB/xhtml/(?:introduction|chapter\d+(?:-\d+)?|appendix002)\.xhtml$',
 'japanese-cooking-shizuo-tsuji': r'OEBPS/xhtml/Tsuj_9781568366166_epub3_(?:itr|prf2?|c\d+|app)_r1\.xhtml$',
 'liquid-intelligence-dave-arnold': r'OEBPS/\d+_(?:Introduction|Part\d+|Ch\d+)\.xhtml$',
 'sift-the-elements-of-great-baking-nicola-lamb': r'text/part0(?:00[6-9]|0[12]\d|03[01]|03[6-9]|0[4-9]\d|1[0-7]\d)(?:_split_\d+)?\.html$',
 'the-art-of-simple-food-alice-waters': r'OEBPS/Wate_9780307885586_epub_(?:itr|c\d+|p\d+|gls)_r1(?:_split_\d+)?\.htm$',
 'the-book-of-cocktail-ratios-michael-ruhlman': r'e9781668003411/xhtml/(?:intro(?:_sub\d+a?)?|fm02(?:_sub\d+)?|part\d+a?|ch\d+[ab]?(?:_(?:rec|sub)\d+)?|appendix\d+(?:_sub\d+)?)\.xhtml$',
 'the-food-lab-better-home-cooking-through-j-kenji-lopez-alt': r'text/part00(?:0[4-9]|1[0-5])(?:_split_\d+)?\.html$',
 'the-kitchen-diaries-nigel-slater': r'OEBPS/(?:january|february|march|april|may|june|july|august|september|october|november|december)(?:_split_\d+)?\.html$',
 'the-meathead-method-meathead': r'OEBPS/text/9780063272859_(?:Introduction|Chapter_\d+|Recipe_\d+a?|Appendix)\.xhtml$',
 'the-zuni-cafe-cookbook-judy-rodgers': r'OEBPS/Text/(?:intro|ch\d+)\.xhtml$',
 'the-wok-recipes-and-techniques-j-kenji-lopez-alt': r'OEBPS/xhtml/(?:Introductiona?|Frontmatter04|Chapter\d+)\.xhtml$',
}

for volume, isbn in ((1, '9780307958174'), (2, '9780307958181')):
    book_id = f'mastering-the-art-of-french-cooking-volume-{volume}-julia-child'
    MEMBERS[book_id] = rf'OEBPS/Chil_{isbn}_epub_(?:frw|itr|fm[2-8]|c\d+a?|app1?)_r1\.htm$'
    PROFILES[book_id] = dict(
        titles='section section1 section2' if volume == 1 else 'recipe_title recipe_title1 recipe_title2 subrecipe_title subrecipe_title1',
        ingredients='hanging bl_hanging' if volume == 1 else 'ingredients1',
        instructions='nonindent' if volume == 1 else 'instruction1 instruction',
        yields='extract1' if volume == 1 else 'serving_size',
        groups='section_subhead' if volume == 1 else 'ingredientshead',
        headnotes='indent bl_nonindent' if volume == 1 else 'headnote translation indent',
        barriers='chapter chapter1 chapter2 subchapter section3' if volume == 1 else 'chapter section',
        non_title_pattern=r'^(?:OTHER )?VARIATIONS?\b' if volume == 1 else None,
        equipment_pattern=r'^(?:(?:A|An|Another|The|Chilled|Hot|Warm|Buttered|Oiled|Optional:|Special equipment:)\b).*(?:saucepan|casserole|skillet|bowl|soup cups|sieve|spatula|whisk|spoon|pan\b|thermometer|dish\b|dishes\b|platter|tureen|foil|paper|rack\b|board|string|cheesecloth|bag\b|mold\b|ramekin|kettle|cutter|blender|processor|rolling pin)',
        dependency_ingredient_pattern=r'(?i)^(?:Ingredients? for|The ingredients? (?:for|in)|All ingredients? (?:for|in)|\d+\)\s*(?:Follow|Increase|Use|Proceed))',
        bibliography=dict(title=f'Mastering the Art of French Cooking, Volume {volume}', author='Julia Child; Louisette Bertholle; Simone Beck' if volume == 1 else 'Julia Child; Simone Beck'),
        bibliography_evidence='Edition title pages visually verified; docs/french-cooking-editions.md.')

PROFILES['the-new-book-of-middle-eastern-food-claudia-roden'] = dict(
    titles='', ingredients='', instructions='', yields='', groups='', headnotes='',
    barriers='chapter part', bold_paragraph_titles=True)
MEMBERS['the-new-book-of-middle-eastern-food-claudia-roden'] = r'OEBPS/Text/Rode_9780307558565_epub_(?:itr|fm[12]|c\d+|app)_r1\.htm$'

# Source paragraphs in this edition collapse several ingredients into one
# paragraph and use the same styles for lists and methods. Keep recipe/prose
# passages until a separately reviewed structural transcription is available.
PROFILES['the-new-book-of-middle-eastern-food-claudia-roden']['knowledge_only_reason'] = 'Flattened ingredient and method markup; source also joins multiple ingredient lines. No inferred lists promoted.'

PROFILES['recipetin-eats-dinner-nagi-maehashi'] = dict(
    titles='', ingredients='', instructions='', yields='', groups='', headnotes='', barriers='',
    fixed_layout_spans=True,
    knowledge_only_reason='Fixed-layout page fragments; columns, wrapped ingredient lines and custom-font footnote glyphs require visual recipe assembly. Retained as unverified source passages, not structured recipes.')
MEMBERS['recipetin-eats-dinner-nagi-maehashi'] = r'OEBPS/page(?:00[7-9]|0[1-9]\d|[12]\d\d|3[0-4]\d|35[0-3]|35[7-9]|36[0-3])\.xhtml$'


def fixed_layout_blocks(member, doc):
    """Retain native text runs without collapsing line/column separators.

    DOM run order and CSS coordinates remain evidence, not an inferred recipe
    reading order. Only top-level positioned runs are emitted; their inline
    children (word spacing/links) remain inside the same source run.
    """
    import re
    from urllib.parse import unquote, urlsplit
    from .importer import clean, resolve
    tree = doc.getroottree()
    spans = doc.xpath('//div[starts-with(@id,"TextContainer")]//span[contains(concat(" ",normalize-space(@class)," ")," s1 ")]')
    page_text = ' '.join(clean(n.text_content()) for n in spans[:4])
    marker = re.search(r'\bPAGE\s+(\d+)\b', page_text)
    page = marker[1] if marker else None
    selected = {tree.getpath(n) for n in spans + doc.xpath('//body//img')}
    result = []
    for ordinal, n in enumerate(doc.iter()):
        if not isinstance(n.tag,str) or tree.getpath(n) not in selected: continue
        text = clean(n.text_content()) if n.tag != 'img' else n.get('alt', '')
        b = dict(text=text, tag=n.tag, classes=n.get('class','').split(), path=member,
                 anchors=([n.get('id')] if n.get('id') else []) + n.xpath('.//*[@id]/@id'),
                 pages=[page] if page else [], element_index=ordinal, xpath=tree.getpath(n),
                 ancestor_classes=[c for a in n.iterancestors() for c in a.get('class','').split()],
                 links=[dict(text=clean(a.text_content()), path=resolve(member,a.get('href')),
                             anchor=unquote(urlsplit(a.get('href')).fragment), href=a.get('href'))
                        for a in n.xpath('.//a[@href]')], layout_style=n.get('style',''))
        if n.tag == 'img':
            b.update(kind='image', assets=[resolve(member,n.get('src',''))])
        result.append(b)
    return result

PROFILES['mastering-the-art-of-french-cooking-volume-1-julia-child']['recipe_member_pattern'] = r'OEBPS/Chil_9780307958174_epub_c\d+a?_r1\.htm$'
PROFILES['the-art-of-simple-food-alice-waters']['instruction_before_first_ingredient'] = True
PROFILES['cooking-at-home-david-chang-priya-krishna']['knowledge_only_reason'] = 'Recipes are written as flexible prose rather than separate measured ingredient lists; retained as source passages.'
PROFILES['sift-the-elements-of-great-baking-nicola-lamb']['source_warnings'] = {
    title: 'Multiple alternative recipes share this heading; retained together as supporting text pending separate variant assembly.'
    for title in ('MERINGUES', 'TART PASTRY', 'TART PASTRYContinued')}

PROFILES['the-wok-recipes-and-techniques-j-kenji-lopez-alt'].update(
    bibliography=dict(title='The Wok: Recipes and Techniques', author='J. Kenji López-Alt'),
    bibliography_evidence='OEBPS/xhtml/Title.xhtml and Copyright.xhtml: title, subtitle, author and 2022 copyright.')
PROFILES['japanese-cooking-shizuo-tsuji'].update(
    bibliography=dict(title='Japanese Cooking: A Simple Art', author='Shizuo Tsuji'),
    bibliography_evidence='OEBPS/xhtml/Tsuj_9781568366166_epub3_tp_r1.xhtml: title and author; removes trailing metadata semicolon.')

PROFILES['hot-thai-kitchen-pailin-chongchitnant'].update(
    ingredients='ingredients', groups='IL_subheader tab1_head',
    title_instruction_classes={},
    title_barrier_exceptions={'Candied Cassava': ('CANDIED CASSAVA', 'SALTED COCONUT SAUCE')},
    role_patterns=(('ingredients', r'^(?:Amount as called for|See instructions for amounts)', 'note'), ('recipe_box_extract', r'^Combine all the ingredients in a small glass jar', 'instruction')))
PROFILES['liquid-intelligence-dave-arnold'].update(
    ingredients='Recipe_ing Recipe_ing1', shared_prose='noindent', method_start='Recipe_rH3',
    title_instruction_classes={title: 'Recipe_stpsV2' for title in ('All-Purpose Sweet-and-Sour', 'Quinine Simple Syrup')})
PROFILES['the-food-lab-better-home-cooking-through-j-kenji-lopez-alt']['title_instruction_classes'] = {
    title: 'sidebar_noind' for title in ('FRY SAUCE', 'THE BEST EGG SALAD')}
