"""Conservative Thai Food assembly over retained Docling evidence.

These are review candidates, never proof of recipe completeness. Layout and
role decisions are derived metadata; original blocks and OCR cells stay intact.
"""
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path

from .pdf_candidates import candidate_blocks

VERSION = 'thai-pdf-assembly-2'
INGREDIENT = re.compile(r'^(?:\d|[¼½¾⅓⅔⅛⅜⅝⅞]|(?:a |an |large |small |generous )?(?:pinch|handful|bunch|sprig|few|little)|salt(?:\s*(?:,|$)|\s+(?:and|to taste|as needed|for)\b)|oil for\b|extra \d)', re.I)
METHOD = re.compile(r'^(?:first[, ]|to (?:make|prepare|cook|serve)\b|(?:make|prepare|wash|heat|clean|cut|peel|pound|mix|combine|add|bring|simmer|steam|fry|deep-fry|crack|roast|bone|soak|grill|boil|rinse|slice|serve|stir|strain|drain|meanwhile|finally|finish|check|blanch|chop|remove|season|work|spoon)\b)', re.I)
GROUPS = {'paste', 'curry paste', 'dressing', 'sauce', 'garnish', 'garnishes', 'filling', 'batter', 'marinade', 'to serve', 'stock', 'syrup', 'garlic paste', 'pastry', 'outer pastry', 'inner pastry', 'topping', 'soup'}


def stable(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def squash(text):
    return re.sub(r'\s+', '', text)


def box(rect, height):
    xs = [rect[f'r_x{i}'] for i in range(4)]
    ys = [rect[f'r_y{i}'] for i in range(4)]
    if rect['coord_origin'] == 'BOTTOMLEFT':
        ys = [height-y for y in ys]
    return min(xs), min(ys), max(xs), max(ys)


def recover_lines(block, pages):
    """Recover line boundaries only when OCR characters match the block exactly.

    Failure leaves the full Docling block untouched. No OCR engine's text is
    silently substituted for another's; line refs preserve the original cells.
    """
    matched = []
    for prov in block['provenance']:
        page = pages[prov['page_no']]
        height = page['size']['height']
        b = prov['bbox']
        top, bottom = (height-b['t'], height-b['b']) if b['coord_origin'] == 'BOTTOMLEFT' else (b['t'], b['b'])
        for ordinal, cell in enumerate(page['cells']):
            l, t, r, bot = box(cell['rect'], height)
            if b['l']-1 <= (l+r)/2 <= b['r']+1 and top-1 <= (t+bot)/2 <= bottom+1 and cell['text'].strip():
                matched.append(dict(text=cell['text'], pdf_page=prov['page_no'],
                                    cell_index=ordinal, rect=cell['rect'], top=t, left=l))
    matched.sort(key=lambda c: (c['pdf_page'], c['top'], c['left']))
    text = block['text']
    if not matched or squash(''.join(c['text'] for c in matched)) != squash(text):
        return None
    # Map non-whitespace OCR lengths into the unchanged Docling string.
    positions = [i for i, char in enumerate(text) if not char.isspace()]
    offset = 0
    result = []
    for cell in matched:
        size = len(squash(cell['text']))
        start, end = positions[offset], positions[offset+size-1]+1
        result.append(dict(text=text[start:end], charspan=[start, end],
                           pdf_pages=[cell['pdf_page']], ocr_cell=cell))
        offset += size
    return result


def load_extraction(root, adapter_version=VERSION):
    root = Path(root)
    manifest = json.loads((root/'manifest.json').read_text())
    blocks, pages, reports = [], {}, []
    for path in sorted(root.glob('*.run.json')):
        run = json.loads(path.read_text())
        stem = path.name.removesuffix('.run.json')
        document_path, lines_path = root/(stem+'.docling.json'), root/(stem+'.lines.json')
        for p, key in [(document_path, 'snapshot_sha256'), (lines_path, 'lines_sha256')]:
            if hashlib.sha256(p.read_bytes()).hexdigest() != run[key]:
                raise ValueError('Extraction snapshot hash mismatch')
        source = {**manifest, 'group': stem, 'snapshot': document_path.name,
                  'snapshot_sha256': run['snapshot_sha256'], 'lines_snapshot': lines_path.name,
                  'lines_sha256': run['lines_sha256'], 'extractor': adapter_version}
        document = json.loads(document_path.read_text())
        chunk_pages = json.loads(lines_path.read_text())
        expected = set(range(run['first'], run['last']+1))
        if {p['pdf_page'] for p in chunk_pages} != expected or set(map(int, document['pages'])) != expected:
            raise ValueError('Missing extraction page')
        for page in chunk_pages:
            if page['pdf_page'] in pages:
                raise ValueError('Duplicate extraction page')
            pages[page['pdf_page']] = page
        chunk_blocks = candidate_blocks(document, source)
        expected_nodes = sum(len(document.get(k, [])) for k in ('texts', 'pictures', 'tables'))
        if len(chunk_blocks) != expected_nodes or document.get('furniture', {}).get('children'):
            raise ValueError('Unaccounted document nodes require an explicit retention policy')
        blocks.extend(chunk_blocks)
        reports.append(run)
    if set(pages) != set(range(1, manifest['pages']+1)):
        raise ValueError('Whole-book assembly requires every page; extraction is incomplete')
    return manifest, blocks, pages, reports


def printed_labels(pages):
    labels = {}
    for number, page in pages.items():
        values = []
        for cell in page['cells']:
            l, t, r, b = box(cell['rect'], page['size']['height'])
            if t > .88*page['size']['height'] and re.fullmatch(r'\d{1,3}', cell['text'].strip()):
                values.append(cell['text'].strip())
        labels[number] = values[0] if len(set(values)) == 1 else None
    return labels


def special_heading(text):
    text = text.strip().lower().rstrip(':')
    if (text.startswith(('accompany with', 'serve with', 'good accompaniments'))
            or re.match(r'^(?:it|this \w+) can (?:also )?be served with', text)):
        return 'accompaniment'
    if text in GROUPS:
        return 'ingredient_group'
    return None


def assemble(blocks, pages, labels=None, content_range=(19, 649), recipe_min_page=157):
    """Partition each source block exactly once, then derive recipe candidates."""
    labels = labels or printed_labels(pages)
    sections, excluded, current = [], [], None
    for block in blocks:
        first = min(block['pdf_pages'])
        if first < content_range[0] or first > content_range[1]:
            excluded.append(dict(block_id=block['id'], reason='front_matter_or_reference_material'))
            continue
        # In this edition Docling calls some genuine glossary titles page
        # headers. Retain headers as evidence rather than erasing boundaries.
        # Original labels remain unchanged in raw_blocks for source review.
        if block['kind'] in {'page_footer', 'picture'}:
            excluded.append(dict(block_id=block['id'], reason=block['kind']))
            continue
        # This edition uses all-caps recipe/section titles. Docling sometimes
        # labels an ingredient or lowercase component as a section header.
        # Do not let that prediction truncate the surrounding recipe.
        letters = re.sub('[^A-Za-z]', '', block['text'])
        heading = (len(letters) > 3 and letters.isupper() and len(block['text']) < 200
                   and not special_heading(block['text']))
        title_text = block['text']
        if block['kind'] in {'section_header', 'title', 'page_header'} and not heading:
            # Upstream can join the English title and lower-case Thai subtitle.
            # Keep the joined original and record only the derived title slice.
            mixed = re.match(r"^([A-Z][A-Z '’(),&/\-]+)\s+[a-z]", title_text)
            if mixed and len(mixed[1].strip()) > 3:
                heading, title_text = True, mixed[1].strip()
        if heading or current is None:
            if current is not None:
                current['end_boundary'] = 'next_heading'
            current = dict(id=stable(block['id']), title=title_text if heading else 'Untitled continuation',
                           has_title=heading, raw_blocks=[], end_boundary='content_end')
            sections.append(current)
        current['raw_blocks'].append(deepcopy(block))
    recipes, technique = [], []
    for section in sections:
        phase, derived, flags = 'intro', [], []
        if any(b['kind'] == 'page_header' for b in section['raw_blocks']):
            flags.append('parser_header_retained_needs_review')
        for index, block in enumerate(section['raw_blocks']):
            if index == 0 and section['has_title']:
                derived.append(dict(kind='title', text=block['text'], raw_block_id=block['id'], pdf_pages=block['pdf_pages']))
                continue
            special = special_heading(block['text'])
            if (not special and phase == 'ingredients' and block['kind'] == 'section_header'
                    and not METHOD.match(block['text']) and not INGREDIENT.match(block['text'])):
                special = 'ingredient_group'
            if special:
                phase = 'notes' if special == 'accompaniment' else 'ingredients'
                derived.append(dict(kind=special, text=block['text'], raw_block_id=block['id'], pdf_pages=block['pdf_pages']))
                continue
            lines = recover_lines(block, pages)
            # Split ingredient text into original OCR lines before classifying;
            # methods/headnotes remain whole paragraphs with every page ref.
            # Unquantified ingredients (e.g. oil for frying, herb offcuts) are
            # often short noun lines between quantities. Retain these in the
            # list until a method opener or prose sentence is observed.
            unquantified = (phase == 'ingredients' and len(block['text']) < 180
                            and not re.search(r'[.!?]$', block['text'].strip())
                            and not METHOD.match(block['text']))
            may_be_ingredients = phase in {'intro', 'ingredients'} and (bool(INGREDIENT.match(block['text'])) or unquantified)
            pieces = lines if may_be_ingredients and lines else [dict(text=block['text'], pdf_pages=block['pdf_pages'])]
            if may_be_ingredients and lines is None:
                flags.append('ingredient_line_boundaries_unresolved')
            for piece in pieces:
                text = piece['text']
                if phase == 'notes':
                    kind = 'note'
                elif METHOD.match(text) and phase == 'ingredients':
                    kind, phase = 'instruction', 'instructions'
                elif INGREDIENT.match(text) and phase in {'intro', 'ingredients'}:
                    kind, phase = 'ingredient', 'ingredients'
                elif unquantified and piece is pieces[0]:
                    kind, phase = 'ingredient', 'ingredients'
                elif phase == 'ingredients' and may_be_ingredients:
                    kind = 'ingredient_continuation'
                elif phase == 'ingredients':
                    kind, phase = 'instruction', 'instructions'
                else:
                    kind = 'instruction' if phase == 'instructions' else 'headnote'
                derived.append(dict(kind=kind, text=text, raw_block_id=block['id'],
                                    pdf_pages=piece['pdf_pages'],
                                    charspan=piece.get('charspan'), ocr_cell=piece.get('ocr_cell')))
        ingredients, instructions = [], []
        for b in derived:
            if b['kind'] == 'ingredient':
                ingredients.append(b['text'])
            elif b['kind'] == 'ingredient_continuation' and ingredients:
                ingredients[-1] += ' ' + b['text']
            elif b['kind'] == 'instruction':
                instructions.append(b['text'])
        touched = sorted({p for b in section['raw_blocks'] for p in b['pdf_pages']})
        # The first four chapters are narrative/history/rice technique in this
        # edition. Numeric prose and map scales there are not recipe lists.
        if ingredients and touched[0] >= recipe_min_page and section['title'] != 'SEASONING THE DRESSING':
            if not instructions: flags.append('missing_method')
            if not section['has_title']: flags.append('missing_title')
            if section['end_boundary'] == 'content_end': flags.append('unverified_end_boundary')
            if any(re.search(r'\d{2,}/[2348]\b|\d%\b|�', b['text']) for b in derived):
                flags.append('suspicious_quantity_or_ocr')
            # Automatic role assignment is always provisional, even with no
            # structural warning: OCR completeness requires independent review.
            recipes.append(dict(id=section['id'], book_id='thai-food', book='Thai Food', author='David Thompson',
                title=section['title'], ingredients=ingredients, instructions=instructions,
                blocks=derived, raw_blocks=section['raw_blocks'], pdf_pages=touched,
                printed_pages={str(p): labels.get(p) for p in touched},
                source={**section['raw_blocks'][0]['source'], 'pdf_page': touched[0], 'printed_page': labels.get(touched[0])},
                source_text='\n\n'.join(b['text'] for b in section['raw_blocks']),
                warnings=sorted(set(flags)), status='candidate_needs_source_review',
                verified_complete_recipe=False, personally_tested=None, end_boundary=section['end_boundary']))
        else:
            technique.append({**section, 'blocks': derived, 'pdf_pages': touched,
                              'status': 'technique_or_unresolved_recipe', 'verified_complete_recipe': False})
    owned = [b['id'] for s in sections for b in s['raw_blocks']] + [b['block_id'] for b in excluded]
    if len(owned) != len(set(owned)) or set(owned) != {b['id'] for b in blocks}:
        raise ValueError('Source block accounting failed')
    return recipes, technique, excluded
