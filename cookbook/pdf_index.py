"""Retained PDF book indexes, separate from recipes and search indexes.

The layout profile below is reviewed for one edition of Thai Food. OCR-derived
hierarchy and destinations remain provisional; raw cells are never rewritten.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import re
import unicodedata

from .pdf_assembly import box

VERSION = 'pdf-book-index-1'
THAI_SHA256 = '66e57673ca2790be225e8d8f40008f934a8a4484257ff0e87ee53a0a39309f5f'
THAI_INDEX_PAGES = tuple(range(662, 692))
# Both right columns contain only children of the topic at the foot of the
# preceding column (prawns / salads). Checked against rendered source pages;
# their leftmost line must not be mistaken for a new root-topic margin.
THAI_COLUMN_BASES = {(680, 1): 218.1, (683, 1): 228.9}
LOCATORS = r'\d{1,3}(?:\s*[-–—,]\s*\d{1,3})*'
TAIL = re.compile(r'\s+(' + LOCATORS + r')\s*$')
SEE = re.compile(r'\bsee(?P<also>\s+also)?\s+', re.I)


def normalized(text):
    text = unicodedata.normalize('NFKD', text.casefold())
    return ' '.join(re.findall(r'[a-z0-9]+', text))


def printed_references(text):
    """Expand printed ranges only; never infer a PDF offset or repair OCR."""
    refs = []
    for part in text.split(','):
        part = part.strip()
        match = re.fullmatch(r'(\d{1,3})(?:\s*[-–—]\s*(\d{1,3}))?', part)
        if not match:
            raise ValueError('Unparsed printed locator: ' + part)
        first, last = match.groups()
        start = int(first)
        end = start
        if last:
            end = int(first[:-len(last)] + last) if len(last) < len(first) else int(last)
            if len(last) < len(first) and end < start:
                end += 10 ** len(last)
        if end < start or end - start >= 100:
            raise ValueError('Suspicious printed page range: ' + part)
        refs.append(dict(source_text=part, printed_pages=[str(p) for p in range(start, end + 1)],
                         locator_kind='unknown_text_or_photograph'))
    return refs


def parse_lines(lines, source_sha256):
    """Parse the edition's two-level index and hanging continuation lines.

    Each input line must already have a column-relative indent level. Preserve
    column/page-spanning parents and wrapped references. Ambiguity is flagged,
    never used to silently choose a recipe.
    """
    groups, pending = [], []
    for line in lines:
        text = line['text'].strip()
        if pending:
            previous = pending[-1]
            joined = ' '.join(x['text'] for x in pending)
            more_pages = bool(re.fullmatch(LOCATORS + r',?', text))
            # A parent topic without locators (e.g. "bamboo") is not a
            # wrapped title. Long incomplete lines and deeper hanging lines
            # continue an entry; comma-ended locators continue as well.
            incomplete = not TAIL.search(joined) and not SEE.search(joined)
            wrapped = (line['level'] > pending[0]['level'] and
                       (pending[0]['level'] >= 1 or previous['width_fraction'] > .72))
            if (more_pages or (joined.rstrip().endswith(',') and not SEE.match(text) and line['level'] >= pending[0]['level'])
                    or (wrapped and (incomplete or SEE.search(joined)))):
                pending.append(line)
                continue
            groups.append(pending)
        pending = [line]
    if pending:
        groups.append(pending)

    entries, root = [], None
    roots = {}
    for group in groups:
        original = '\n'.join(x['text'] for x in group)
        joined = ' '.join(x['text'].strip() for x in group)
        first = group[0]
        eid = 'pdf_index_' + hashlib.sha256(
            f"{source_sha256}:{first['pdf_page']}:{first['cell_index']}".encode()).hexdigest()[:20]
        see = SEE.search(joined)
        body = joined[:see.start()].strip() if see else joined
        match = TAIL.search(body)
        label = body[:match.start()].strip() if match else body
        continued = bool(re.search(r'\(continued\)\s*$', label, re.I))
        label = re.sub(r'\s*\(continued\)\s*$', '', label, flags=re.I)
        refs, flags = [], []
        if match:
            try:
                refs = printed_references(match[1])
            except ValueError:
                flags.append('unparsed_printed_locator')
        elif re.search(r'\d', body):
            flags.append('possible_ocr_locator_error')
        continuation_of = roots.get(normalized(label)) if continued else None
        if continued and not continuation_of:
            flags.append('unresolved_continued_topic')
        parent = root if first['level'] >= 1 else None
        if first['level'] >= 2:
            flags.append('orphan_hanging_line')
        if first['level'] >= 1 and root is None:
            flags.append('missing_parent_topic')
        if not label and see:
            # An indented standalone "see also" belongs to the enclosing
            # topic, not the immediately preceding recipe subentry.
            kind = 'cross_reference'
            if not parent:
                flags.append('missing_cross_reference_owner')
        else:
            kind = 'entry'
        cross = []
        if see:
            target_text = joined[see.end():].strip()
            for target in target_text.split(';'):
                cross.append(dict(relation='see_also' if see['also'] else 'see',
                                  target_label=target.strip(), target_entry_ids=[],
                                  status='unresolved', source_text=target_text))
        entry = dict(id=eid, label=label, normalized_label=normalized(label),
                     source_text=original, lines=deepcopy(group), parent_id=parent,
                     continuation_of=continuation_of, kind=kind, page_references=refs,
                     cross_references=cross, flags=flags, status='index_ocr_needs_review',
                     verified_recipe_membership=False)
        entries.append(entry)
        if first['level'] == 0 and label:
            root = continuation_of or eid
            roots.setdefault(normalized(label), root)
    by_id = {e['id']: e for e in entries}
    for e in entries:
        parent = by_id.get(e['parent_id'])
        e['hierarchy'] = ([parent['label']] if parent else []) + ([e['label']] if e['label'] else [])
    root_labels = defaultdict(list)
    for e in entries:
        if e['label'] and not e['parent_id'] and not e['continuation_of']:
            root_labels[e['normalized_label']].append(e['id'])
    for e in entries:
        for ref in e['cross_references']:
            ids = root_labels.get(normalized(ref['target_label']), [])
            ref.update(target_entry_ids=ids,
                       status='unique_label_match' if len(ids) == 1 else 'ambiguous_label' if ids else 'unresolved')
    return entries


def resolve_destinations(entries, labels, recipes, sections):
    """Keep whole-page candidates separate from uniquely matched titles.

    Even single-recipe pages do not establish membership: an index reference
    can name a technique, photograph, accompaniment or variation on that page.
    """
    page_map = defaultdict(list)
    for pdf_page, printed in sorted(labels.items()):
        if printed is not None:
            page_map[str(printed)].append(pdf_page)
    for entry in entries:
        for reference in entry['page_references']:
            destinations = []
            for printed in reference['printed_pages']:
                pdf_pages = page_map.get(printed, [])
                page_recipes = [r for r in recipes if set(pdf_pages) & set(r['pdf_pages'])]
                page_sections = [s for s in sections if set(pdf_pages) & set(s['pdf_pages'])]
                exact = [r['id'] for r in page_recipes if normalized(r['title']) == entry['normalized_label']]
                status = ('unmapped_printed_page' if not pdf_pages else
                          'ambiguous_printed_label' if len(pdf_pages) > 1 else
                          'unique_title_candidate' if len(exact) == 1 else
                          'ambiguous_title' if exact else
                          'page_candidates_only' if page_recipes or page_sections else 'page_without_candidate_content')
                destinations.append(dict(printed_page=printed, pdf_pages=pdf_pages,
                    status=status, page_recipe_candidate_ids=[r['id'] for r in page_recipes],
                    page_section_candidate_ids=[s['id'] for s in page_sections],
                    title_matched_recipe_candidate_ids=exact if status == 'unique_title_candidate' else [],
                    verified_recipe_ids=[]))
            reference['destinations'] = destinations


def capture_thai_index(manifest, blocks, pages, labels, recipes, sections):
    if manifest['sha256'] != THAI_SHA256:
        raise ValueError('Index layout requires the reviewed Thai Food edition; add a reviewed profile')
    if not set(THAI_INDEX_PAGES) <= set(pages):
        raise ValueError('Missing index pages; full PDF imports must retain their index')
    lines, excluded = [], []
    raw_pages = [deepcopy(pages[n]) for n in THAI_INDEX_PAGES]
    for n in THAI_INDEX_PAGES:
        page = pages[n]
        columns = [[], []]
        for i, cell in enumerate(page['cells']):
            l, t, r, b = box(cell['rect'], page['size']['height'])
            text = cell['text'].strip()
            line = dict(pdf_page=n, printed_page=labels.get(n), cell_index=i,
                        text=cell['text'], rect=deepcopy(cell['rect']), left=l, top=t,
                        width_fraction=(r-l)/(page['size']['width']*.45))
            reason = ('empty' if not text else 'page_footer' if t > page['size']['height']*.9 else
                      'index_heading_or_legend' if text in {'INDEX', 'Numbers in bold indicate photographs.'} else
                      'alphabet_divider' if re.fullmatch('[a-z]', text) else None)
            if reason:
                excluded.append(dict(line, reason=reason))
            else:
                # The right column starts just left of 50% on some rectos.
                columns[int(l >= page['size']['width']*.48)].append(line)
        for column, cells in enumerate(columns):
            if not cells:
                continue
            base = THAI_COLUMN_BASES.get((n, column), min(c['left'] for c in cells))
            for line in sorted(cells, key=lambda x: (x['top'], x['left'])):
                indent = line['left']-base
                line.update(column=column, level=0 if indent < 7 else 1 if indent < 20 else 2)
                lines.append(line)
    entries = parse_lines(lines, manifest['sha256'])
    resolve_destinations(entries, labels, recipes, sections)
    covered = [(l['pdf_page'], l['cell_index']) for e in entries for l in e['lines']]
    covered += [(l['pdf_page'], l['cell_index']) for l in excluded]
    expected = {(n, i) for n in THAI_INDEX_PAGES for i in range(len(pages[n]['cells']))}
    if len(covered) != len(set(covered)) or set(covered) != expected:
        raise ValueError('Index OCR cell accounting failed')
    raw_blocks = [deepcopy(b) for b in blocks if set(b['pdf_pages']) & set(THAI_INDEX_PAGES)]
    refs = [r for e in entries for r in e['page_references']]
    cross = [r for e in entries for r in e['cross_references']]
    destinations = [d for r in refs for d in r['destinations']]
    report = dict(version=VERSION, status='retained_for_review', source_sha256=manifest['sha256'],
        index_pdf_pages=list(THAI_INDEX_PAGES), index_printed_labels=[labels.get(n) for n in THAI_INDEX_PAGES],
        raw_source_blocks=len(raw_blocks), raw_ocr_cells=len(expected), every_ocr_cell_accounted_once=True,
        entries=len(entries), nested_entries=sum(e['parent_id'] is not None for e in entries),
        continued_topics=sum(bool(e['continuation_of']) for e in entries),
        locator_groups=len(refs), printed_page_destinations=len(destinations),
        cross_references=len(cross), cross_reference_status_counts=dict(Counter(r['status'] for r in cross)),
        destination_status_counts=dict(Counter(d['status'] for d in destinations)),
        flag_counts=dict(Counter(f for e in entries for f in e['flags'])),
        verified_recipe_memberships=0,
        limitation='OCR does not reliably retain bold photograph locators; all locator types remain unknown. Hierarchy and title associations are provisional, never ingredient membership or exclusions.')
    return dict(version=VERSION, source=deepcopy(manifest), report=report, entries=entries,
                layout_profile=dict(index_pdf_pages=list(THAI_INDEX_PAGES), columns=2,
                    column_split_width_fraction=.48,
                    reviewed_root_margin_overrides=[dict(pdf_page=n, column=c, left=x)
                        for (n,c),x in THAI_COLUMN_BASES.items()]),
                raw_pages=raw_pages, raw_blocks=raw_blocks, excluded_lines=excluded,
                legend='Numbers in bold indicate photographs.', locator_style_recovered=False)
