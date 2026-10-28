"""Assemble native PDF candidates from an edition-bound layout profile.

This is a candidate adapter, not a general PDF classifier. The profile supplies
reviewed boundaries; glyphs, links and original lines remain in the capture.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import re

from cookbook.source_artifacts import is_source_artifact

VERSION = 'native-pdf-candidate-1'


def apply_owner_decisions(records, ledger):
    """Apply exact source-anchored interpretations without rewriting evidence."""
    if any(r['source']['sha256'] != ledger['source_sha256'] for r in records):
        raise ValueError('Owner decision edition mismatch')
    result = deepcopy(records)
    changed = set()
    for decision in ledger['decisions']:
        for edit in decision['edits']:
            matches = [(r, i, b, j, line) for r in result for i, b in enumerate(r['blocks'])
                       for j, line in enumerate(b['source_lines'])
                       if anchor(line) == (edit['pdf_page'], edit['line_index'])]
            if not matches:
                raise ValueError('Owner decision anchor missing')
            for r, i, b, j, line in matches:
                interpreted = b.setdefault('owner_interpreted_lines', deepcopy(b['source_lines']))
                if line['text'] != edit['before'] or interpreted[j]['text'] != edit['before']:
                    raise ValueError('Stale or duplicate owner decision')
                b.setdefault('source_text', b['text'])
                interpreted[j]['text'] = edit['after']
                interpreted[j]['owner_resolution_id'] = decision['id']
                b['text'] = '\n'.join(l['text'] for l in interpreted)
                r['normalizations'].append(dict(kind='owner_interpretation', block_index=i,
                    issue_id=decision['id'], pdf_page=edit['pdf_page'], line_index=edit['line_index'],
                    before=edit['before'], after=edit['after'], decision=decision['decision']))
                resolved = r['review'].setdefault('resolved_source_questions', [])
                if not any(d['id'] == decision['id'] for d in resolved):
                    resolved.append(deepcopy(decision))
                r['warnings'] = [w for w in r['warnings'] if not w.startswith(decision['id'] + ':')]
                changed.add(r['id'])
    by_id = {r['id']: r for r in result}
    for r in result:
        if r['id'] in changed:
            r['ingredients'] = [b['text'] for b in r['blocks'] if b['kind'] == 'ingredient']
            r['instructions'] = [b['text'] for b in r['blocks'] if b['kind'] == 'instruction']
            r['headnote'] = '\n'.join(b['text'] for b in r['blocks'] if b['kind'] == 'headnote')
            r['yield'] = next((b['text'] for b in r['blocks'] if b['kind'] == 'metadata'), None)
        for note in r['supporting_notes']:
            if note['id'] in changed:
                target = by_id[note['id']]
                for field in ('blocks', 'warnings', 'normalizations', 'review'):
                    note[field] = deepcopy(target[field])
    return result


def anchor(line):
    return (line['pdf_page'], line['line_index'])


def link_lines(page, link):
    """Use glyph centres: annotation rectangles can overlap adjacent baselines."""
    x0, y0, x1, y1 = link['rect']
    top, bottom = page['height'] - y1, page['height'] - y0
    return [line['line_index'] for line in page['lines'] if any(
        x0 - .5 <= (c['x0'] + c['x1']) / 2 <= x1 + .5 and
        top - .5 <= (c['top'] + c['bottom']) / 2 <= bottom + .5
        for c in line['spans'])]


def in_ranges(line, ranges):
    return any(tuple(a) <= anchor(line) <= tuple(b) for a, b in ranges)


def step_number(line):
    match = re.match(r'^(\d+)\.?\s', line['text'])
    if match and all('Bold' in c['font'] for c in line['spans'][:len(match[1])]):
        return int(match[1])
    return None


def blocks_for(lines, pages, prose_start=None):
    blocks = []
    phase = 'headnote'
    ingredient_group = None
    for line in lines:
        text = line['text']
        if is_source_artifact(text):
            continue  # Original capture and coverage ledger retain the removal.
        size = line['sizes']
        heading = False
        if size == [21.0]:
            kind = 'title'
        elif size == [17.25]:
            kind = 'metadata'
        elif size == [11.25]:
            phase = 'instruction'
            heading = text.isupper()
            kind = 'ingredient_group' if heading else 'ingredient'
            if heading:
                ingredient_group = text
        elif size == [15.0]:
            if prose_start is not None and anchor(line) >= tuple(prose_start):
                phase = 'instruction'
            if text.startswith(('DO AHEAD', 'NOTE', 'EAT WITH', 'HOW TO HALVE A CHICKEN')):
                phase = 'note'
            if text.startswith('TO MAKE') or text in {'FOR PEPPERONI PIE', 'FOR LEMONY GREENS PIE'}:
                phase = 'instruction'
                heading = True
            kind = phase
        else:
            raise ValueError(f'Unreviewed typography at {anchor(line)}: {size}')
        new = (not blocks or kind != blocks[-1]['kind'] or heading or
               (kind == 'instruction' and step_number(line) is not None) or
               text.startswith(('DO AHEAD', 'NOTE', 'EAT WITH')))
        if not new:
            last = blocks[-1]['source_lines'][-1]
            same_page = last['pdf_page'] == line['pdf_page']
            # Ingredient rows have 18–19 pt leading; wrapped rows have 13.5 pt.
            if kind == 'ingredient':
                new = not same_page or line['top'] - last['top'] > 14
            elif same_page:
                new = line['top'] - last['bottom'] > 10
        if new:
            blocks.append(dict(kind=kind, text='', tag='pdf_excerpt', classes=[],
                path=f"pdf/page/{line['pdf_page']}", anchors=[], pages=[],
                pdf_pages=[], links=[], source_lines=[], ingredient_group=ingredient_group
                if kind in {'ingredient', 'ingredient_group'} else None))
        b = blocks[-1]
        b['text'] += ('\n' if b['text'] else '') + text
        b['source_lines'].append(deepcopy(line))
        b['anchors'].append(f"pdf-{line['pdf_page']}-line-{line['line_index']}")
        if line['pdf_page'] not in b['pdf_pages']:
            b['pdf_pages'].append(line['pdf_page'])
        page = pages[line['pdf_page']]
        for idx, link in enumerate(page['links']):
            if line['line_index'] in link_lines(page, link):
                key = (page['pdf_page'], idx)
                if not any(tuple(x['source_annotation']) == key for x in b['links']):
                    b['links'].append(dict(deepcopy(link), source_annotation=list(key)))
    return blocks


def assemble(pages, outlines, manifest, profile):
    if manifest['sha256'] != profile['sha256']:
        raise ValueError('Edition mismatch')
    by_page = {p['pdf_page']: p for p in pages}
    all_lines = [l for p in pages for l in p['lines']]
    records, contexts, coverage = [], [], {}
    nodes = []
    for i, node in enumerate(outlines):
        if not profile['body_start'] <= node['pdf_page'] <= profile['body_end']:
            continue
        end = outlines[i+1]['pdf_page'] - 1
        nodes.append(dict(title=node['title'], start=node['pdf_page'], end=end))
    for node in nodes:
        lines = [l for l in all_lines if node['start'] <= l['pdf_page'] <= node['end']]
        has_list = any(l['sizes'] == [11.25] for l in lines)
        prose = profile['prose_recipes'].get(str(node['start']))
        if not has_list and prose is None:
            contexts.append(dict(**node, lines=lines, classification='supporting_prose_or_section'))
            for l in lines:
                coverage[anchor(l)] = ['context']
            continue
        node.update(kind='recipe' if has_list else 'prose_recipe',
                    ranges=[[[node['start'], 0], [node['end'], 100000]]],
                    prose_start=prose, parent_start=None,
                    warnings=profile.get('source_notes', {}).get(str(node['start']), []))
        for child in profile['components']:
            if child['parent_start'] == node['start'] and child.get('exclusive'):
                lines = [l for l in lines if not in_ranges(l, child['ranges'])]
        node['lines'] = lines
    candidates = [n for n in nodes if 'lines' in n]
    for child in profile['components']:
        candidates.append(dict(child, lines=[l for l in all_lines if in_ranges(l, child['ranges'])]))
    for node in candidates:
        key = f"{manifest['sha256']}:{node['ranges'][0][0]}:{node['kind']}"
        rid = hashlib.sha256(('native-pdf:' + key).encode()).hexdigest()[:16]
        lines = node['lines']
        blocks = blocks_for(lines, by_page, node.get('prose_start'))
        title = node['title']
        source = dict(file=manifest['file'], sha256=manifest['sha256'], format='pdf',
            member=f"pdf/page/{node['ranges'][0][0][0]}", anchor=rid,
            pdf_page=node['ranges'][0][0][0], printed_page=None, extractor=VERSION,
            locations=[dict(pdf_page=n, printed_page=None) for n in sorted({l['pdf_page'] for l in lines})])
        r = dict(id=rid, book_id=manifest['book_id'], book=manifest['title'], author=manifest['author'],
            title=title, record_kind=node['kind'], aliases=node.get('aliases', []),
            ingredients=[b['text'] for b in blocks if b['kind']=='ingredient'],
            instructions=[b['text'] for b in blocks if b['kind']=='instruction'],
            headnote='\n'.join(b['text'] for b in blocks if b['kind']=='headnote'),
            **{'yield': next((b['text'] for b in blocks if b['kind']=='metadata'), None)},
            source_text='\n'.join(l['text'] for l in lines if not is_source_artifact(l['text'])), source=source, blocks=blocks,
            source_cleanups=[dict(kind='standalone_source_site_watermark',pdf_page=l['pdf_page'],
                line_index=l['line_index'],original_retained_in='native source capture')
                for l in lines if is_source_artifact(l['text'])],
            warnings=list(node.get('warnings', [])), normalizations=[],
            references=[], supporting_notes=[], parent_id=None, component_ids=[], variant_ids=[],
            pdf_pages=sorted({l['pdf_page'] for l in lines}), printed_pages=[],
            review=dict(status='candidate_native_text_and_boundary_checked',
                verified_complete_recipe=False, personally_tested=None,
                ingredient_representation='source rows with explicit group labels; prose ingredients remain in method text'),
            boundary=dict(ranges=node['ranges'], parent_start=node.get('parent_start'),
                exclusive=node.get('exclusive', False)))
        if node['kind']=='prose_recipe':
            r['warnings'].append('Prose recipe: ingredients and quantities remain in the original method; no ingredient list is invented.')
        for l in lines:
            coverage.setdefault(anchor(l), []).append(rid)
        records.append(r)
    bases = {r['source']['pdf_page']: r for r in records if not r['boundary']['parent_start']}
    for r in records:
        if r['boundary']['parent_start']:
            parent = bases[r['boundary']['parent_start']]
            r['parent_id'] = parent['id']
            parent['variant_ids' if r['record_kind']=='variant' else 'component_ids'].append(r['id'])
    by_id = {r['id']: r for r in records}
    for r in records:
        for b in r['blocks']:
            for link in b['links']:
                destination = (link.get('destination') or {}).get('pdf_page')
                targets = [t for t in records if destination in t['pdf_pages'] and
                           (not t['parent_id'] or t['boundary']['exclusive'])]
                link['target_recipe_ids'] = [t['id'] for t in targets]
                for t in targets:
                    if t['id'] != r['id'] and not any(
                            x.get('recipe_id') == t['id'] for x in r['references']):
                        r['references'].append(dict(kind='source_hyperlink', recipe_id=t['id'],
                            title=t['title'], destination=deepcopy(link['destination'])))
    for r in records:
        for kind, ids in [('component', r['component_ids']), ('variation', r['variant_ids']),
                          ('base_recipe', [r['parent_id']] if r['record_kind']=='variant' else [])]:
            for rid in ids:
                target = by_id[rid]
                r['references'].append(dict(kind=kind, recipe_id=rid, title=target['title']))
                r['supporting_notes'].append(dict(id=rid,title=target['title'],relationship=kind,
                    path=target['source']['member'],source=deepcopy(target['source']),
                    blocks=deepcopy(target['blocks']),warnings=deepcopy(target['warnings'])))
    body = [l for l in all_lines if profile['body_start'] <= l['pdf_page'] <= profile['body_end']]
    missing = [anchor(l) for l in body if anchor(l) not in coverage]
    if missing:
        raise ValueError(f'Unassigned body lines: {missing}')
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('Duplicate source identities')
    return records, contexts, dict(version=VERSION, records=len(records),
        kinds=dict(Counter(r['record_kind'] for r in records)), body_lines=len(body),
        unassigned_lines=missing, source_sha256=manifest['sha256'], live_promoted=False,
        coverage=[dict(pdf_page=k[0],line_index=k[1],assignments=v) for k,v in sorted(coverage.items())])
