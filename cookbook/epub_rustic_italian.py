"""Inspected layout for the 2011 Ten Speed Rustic Italian Food EPUB.

This profile describes this source edition only. Variations and alternative
shaping methods stay with their parent as notes, never sequential main steps.
"""

BOOK_ID = 'rustic-italian-food-marc-vetri'
MEMBERS = r'OEBPS/Vetr_9781607740797_epub_(?:frw|itr|col[1-8]|c0[1-8]|bm1)_r1\.htm$'
PROFILE = dict(
    version='epub-batch-rustic-italian-1',
    titles='Recipe_Title', ingredients='IL_item',
    instructions='Method method1', yields='Yield', groups='IL_subheader',
    headnotes='Headnote', notes='method_subheader note_header beverage',
    barriers='section section8 HeaderA HeaderA1 textbox_header',
    nav_classes='chapter_contents',
    restrict_members=True, inline_page_ids=True,
    inherited_source_anchors=True,
    separate_leading_artwork=True,
    qualification_warnings={
        'Biga STARTER': [
            'Original source discrepancy retained: Natural Honey Starter gives ¾ cup (220 g) whole wheat flour; volume and weight conflict. No author correction is established.',
            'Original source typo retained: Durum Focaccia Starter feeding water says 110 to 110°F, unlike the adjacent 100 to 110°F instructions. No correction has been inferred.',
        ],
        'Rustic LOAF': ['Original source discrepancy retained: flour is 2½ cups plus 1 tablespoon (650 g). This conflicts with the source’s other flour equivalents; no quantity has been silently corrected.'],
        'Chocolate BREAD': ['Original source discrepancy retained: 8 egg yolks (16 g) conflicts with the source’s other yolk quantities. No author correction is established; do not treat both measures as equivalent.'],
        'BASIC Extruded PASTA DOUGH': [
            'Original source discrepancy retained: semolina is 2 cups plus 2 tablespoons (500 g); the stated about 1 pound yield is below the 500 g flour plus 220 g water. No quantity or yield correction has been inferred.',
            'Original source wording retained: the SPAGHETTI variation says to cut the bucatini. Treat this as a source wording concern, not an instruction to combine different shapes.',
        ],
        'DURUM Focaccia': ['Original source discrepancy retained: 4 cups (680 g) durum flour differs materially from the durum flour equivalents in Biga Starter. No quantity correction has been inferred; source review is required.'],
        'Rhubarb STRUDEL': ['Original source discrepancy retained: rhubarb is 1½ pounds (26 oz), whereas 1½ pounds equals 24 ounces. No silent correction has been applied.'],
    },
)


def retain_container_anchors(blocks, doc):
    """Bind omitted source IDs to their first contained/following source block.

EPUB links target textbox wrappers and zero-text page anchors. The original
anchor's XPath is retained separately from the receiving block's XPath; IDs
already represented by a source block are never reassigned.
"""
    represented = {a for b in blocks for a in b['anchors']}
    nodes = list(doc.iter())
    order = {node: i for i, node in enumerate(nodes)}
    tree = doc.getroottree()
    for node in nodes:
        anchor = node.get('id') if isinstance(node.tag, str) else None
        if not anchor or anchor in represented:
            continue
        # Only structural wrappers/empty anchor elements in this edition.
        if node.tag not in {'div', 'a', 'span'}:
            continue
        descendants = set(node.iter())
        contained = [b for b in blocks if nodes[b['element_index']] in descendants]
        if contained:
            target = contained[0]
            relation = 'first contained block'
        elif not ''.join(node.itertext()).strip():
            target = next((b for b in blocks if b['element_index'] > order[node]), None)
            relation = 'first following block after empty anchor'
        else:
            target = None
        if target is not None:
            target['anchors'].append(anchor)
            target.setdefault('inherited_anchors', []).append(
                dict(anchor=anchor, xpath=tree.getpath(node), relation=relation))
            represented.add(anchor)


def mark_leading_artwork(blocks):
    """Keep pre-title photographs/captions outside the preceding recipe.

Several index entries point to these photographs, rather than recipe headings.
They remain supporting source evidence, with no inferred recipe membership.
"""
    for i, block in enumerate(blocks):
        if 'Recipe_Title' not in block['classes']:
            continue
        first = i
        while first and (blocks[first - 1].get('kind') == 'image'
                         or 'caption' in blocks[first - 1]['classes']):
            first -= 1
        if first < i:
            blocks[first]['source_boundary'] = 'pre-title artwork retained separately'
