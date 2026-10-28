"""Page-level review evidence for a scanned book that fails recipe assembly QA.

This is deliberately not a recipe assembler. OCR lines and Docling blocks are
separate evidence streams. The edition profile supplies index coverage; uncertain
OCR, typography and missing printed labels cannot manufacture complete recipes.
"""
from collections import Counter
from copy import deepcopy
import re

from .pdf_assembly import box
from .pdf_index import TAIL, printed_references, resolve_destinations, normalized
from .source_artifacts import is_source_artifact

VERSION = 'tu-casa-scan-review-1'
SOURCE_SHA = '27a807a2af8639887b3e6ac12298c294d30a4da340fc2640bd7fe646c0972579'
INDEX_PAGES = (225, 226, 227, 228)


def page_evidence(page):
    """Retain every cell; sort a separate review view, without changing text."""
    n, size = page['pdf_page'], page['size']
    lines, excluded, labels = [], [], []
    for i, cell in enumerate(page['cells']):
        l, t, r, b = box(cell['rect'], size['height'])
        text = cell['text']
        line = dict(pdf_page=n, cell_index=i, text=text,
                    rect=deepcopy(cell['rect']), left=l/size['width'],
                    top=t/size['height'], right=r/size['width'], bottom=b/size['height'])
        # The book's regular recipe layout has one narrow left column and
        # one wide right column. This is a provisional inspection order only.
        line['column'] = int(line['left'] >= .33)
        footer = line['top'] > .93
        if footer and (line['left'] < .2 or line['left'] > .8) and re.fullmatch(r'\d{1,3}', text.strip()):
            labels.append(dict(text=text.strip(), cell_index=i))
        reason = ('source_site_artifact' if is_source_artifact(text) else
                  'empty' if not text.strip() else 'page_footer' if footer else None)
        if reason:
            excluded.append(dict(line, reason=reason))
        else:
            lines.append(line)
    two_column = 17 <= n <= 70
    if two_column:
        lines.sort(key=lambda x: (x['column'], x['top'], x['left'], x['cell_index']))
    values = {x['text'] for x in labels}
    return dict(pdf_page=n, printed_page=next(iter(values)) if len(values) == 1 else None,
                printed_label_observations=labels, raw_page=deepcopy(page),
                review_lines=lines, excluded_lines=excluded,
                review_text='\n'.join(x['text'] for x in lines),
                status='page_evidence_needs_review', verified_complete_recipe=False,
                ordering=('provisional_two_column_geometry' if two_column else 'original_ocr_cell_sequence') + '; not a recipe boundary or text correction')


def capture_index(manifest, blocks, pages, labels):
    """Keep raw four-column index and conservative single-cell locator hints.

    Wrapping, hierarchy and italic illustration locators are not recovered.
    Never claim that a partial OCR line is a complete semantic index entry.
    """
    if manifest['sha256'] != SOURCE_SHA or not set(INDEX_PAGES) <= pages.keys():
        raise ValueError('Missing reviewed Tu Casa Mi Casa index/source')
    if any(not any(c['text'].strip() for c in pages[n]['cells']) for n in INDEX_PAGES):
        raise ValueError('Text-bearing index page lacks OCR evidence')
    records, excluded = [], []
    for n in INDEX_PAGES:
        page = pages[n]
        for i, cell in enumerate(page['cells']):
            l, t, r, b = box(cell['rect'], page['size']['height'])
            text = cell['text']
            line = dict(pdf_page=n, cell_index=i, text=text, rect=deepcopy(cell['rect']))
            reason = ('source_site_artifact' if is_source_artifact(text) else
                      'empty' if not text.strip() else
                      'page_footer' if t/page['size']['height'] > .93 else
                      'heading_or_legend' if text.strip().casefold() == 'index' or 'italics refer' in text else None)
            if reason:
                excluded.append(dict(line, reason=reason))
                continue
            match = TAIL.search(text)
            refs, flags = [], ['single_ocr_line_not_verified_entry', 'hierarchy_and_wrapping_unresolved']
            if match:
                try:
                    refs = printed_references(match[1])
                except ValueError:
                    flags.append('unparsed_printed_locator')
            label = text[:match.start()].strip() if match else text.strip()
            records.append(dict(id=f'tu_casa_index_line_{n}_{i}', label=label,
                normalized_label=normalized(label), source_text=text, lines=[line],
                page_references=refs, flags=flags, status='index_line_needs_review',
                verified_recipe_membership=False))
    resolve_destinations(records, labels, [], [])
    return dict(version=VERSION, source=deepcopy(manifest), index_pdf_pages=list(INDEX_PAGES),
                raw_pages=[deepcopy(pages[n]) for n in INDEX_PAGES],
                raw_blocks=[deepcopy(b) for b in blocks if set(b['pdf_pages']) & set(INDEX_PAGES)],
                locator_records=records, excluded_lines=excluded,
                legend='Page numbers in italics refer to illustrations.',
                locator_style_recovered=False, hierarchy_recovered=False,
                limitation='Raw index captured in full. Single-cell locator hints are not complete entries; wrapping, hierarchy, OCR and destinations require review.')


def build_review(manifest, blocks, pages):
    if manifest['sha256'] != SOURCE_SHA or manifest['pages'] != 233 or set(pages) != set(range(1, 234)):
        raise ValueError('Review profile requires all 233 pages of the hash-bound source')
    evidence = [page_evidence(pages[n]) for n in sorted(pages)]
    labels = {p['pdf_page']: p['printed_page'] for p in evidence}
    index = capture_index(manifest, blocks, pages, labels)
    # Observed-label discontinuities are review prompts, not proof of a missing
    # source page: OCR and photographed facing-page fragments can also cause them.
    observed = [(n, int(v)) for n, v in labels.items() if v is not None]
    jumps = [dict(before_pdf=a, before_printed=x, after_pdf=b, after_printed=y,
                  printed_delta=y-x, pdf_delta=b-a)
             for (a, x), (b, y) in zip(observed, observed[1:]) if y-x != b-a]
    report = dict(version=VERSION, status='extracted_quality_gate_requires_review',
        source_sha256=manifest['sha256'], pages=len(pages), source_blocks=len(blocks),
        ocr_cells=sum(len(p['cells']) for p in pages.values()),
        empty_ocr_pages=[n for n,p in pages.items() if not p['cells']],
        observed_printed_labels=sum(v is not None for v in labels.values()),
        observed_label_discontinuities=jumps,
        index_pdf_pages=list(INDEX_PAGES), index_ocr_cells=sum(len(pages[n]['cells']) for n in INDEX_PAGES),
        index_locator_records=len(index['locator_records']),
        index_records_with_locators=sum(bool(r['page_references']) for r in index['locator_records']),
        source_artifact_cells=sum(l['reason']=='source_site_artifact' for p in evidence for l in p['excluded_lines']),
        source_artifact_blocks=sum(is_source_artifact(b['text']) for b in blocks),
        recipes_promoted=0, verified_complete_recipes=0,
        limitation='Full page/cell retention does not establish recipe completeness or OCR accuracy. No recipe assembly or live promotion until quality review.')
    verify_review(manifest, blocks, pages, evidence, index)
    return evidence, index, labels, report


def verify_review(manifest, blocks, pages, evidence, index):
    """Validate output against independently retained extraction snapshots."""
    if len(evidence) != len(pages) or {p['pdf_page'] for p in evidence} != set(pages):
        raise ValueError('Page evidence coverage mismatch')
    for p in evidence:
        n = p['pdf_page']
        if p['raw_page'] != pages[n] or p['verified_complete_recipe']:
            raise ValueError('Raw page changed or falsely verified')
        lines = p['review_lines'] + p['excluded_lines']
        if Counter(l['cell_index'] for l in lines) != Counter(range(len(pages[n]['cells']))):
            raise ValueError('Cell ownership mismatch')
        for line in lines:
            raw = pages[n]['cells'][line['cell_index']]
            if line['text'] != raw['text'] or line['rect'] != raw['rect']:
                raise ValueError('Derived OCR evidence changed')
        if p['review_text'] != '\n'.join(l['text'] for l in p['review_lines']):
            raise ValueError('Review text mismatch')
    if index['raw_pages'] != [pages[n] for n in INDEX_PAGES]:
        raise ValueError('Raw index pages changed')
    if index['raw_blocks'] != [b for b in blocks if set(b['pdf_pages']) & set(INDEX_PAGES)]:
        raise ValueError('Raw index blocks changed')
    lines = [l for r in index['locator_records'] for l in r['lines']] + index['excluded_lines']
    if Counter((l['pdf_page'], l['cell_index']) for l in lines) != Counter((n,i) for n in INDEX_PAGES for i in range(len(pages[n]['cells']))):
        raise ValueError('Index cell ownership mismatch')
    for line in lines:
        raw = pages[line['pdf_page']]['cells'][line['cell_index']]
        if line['text'] != raw['text'] or line['rect'] != raw['rect']:
            raise ValueError('Index OCR evidence changed')

    # Derived hints must remain reproducible from source cells and observed labels.
    expected_index = capture_index(manifest, blocks, pages, {p['pdf_page']: p['printed_page'] for p in evidence})
    if index != expected_index:
        raise ValueError('Derived index hints changed')
