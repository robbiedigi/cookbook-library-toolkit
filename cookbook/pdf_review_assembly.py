"""Assemble private PDF candidates from explicit, source-reviewed line ranges.

This consumes a review ledger, not model-generated recipe boundaries. Original
page fields remain immutable. Source transcription and owner interpretation are
carried as separate evidence layers; nothing here promotes a live recipe.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib

ROLES = ('title', 'metadata', 'headnote', 'ingredients', 'method', 'notes')
LAYERS = ('owner_interpreted_lines', 'source_reviewed_lines',
          'owner_reviewed_lines', 'review_lines')


def preferred_lines(row):
    return next(row[k] for k in LAYERS if k in row)


def assembled_lines(row):
    return row.get('recipe_owner_interpreted_lines',
                   row.get('recipe_reviewed_lines', preferred_lines(row)))


def selected(lines, ranges):
    """Preserve reviewed order, including insertions anchored to original cells."""
    result = []
    for line in lines:
        anchor = line.get('cell_index')
        if anchor is None:
            anchor = line.get('insert_after_original_cell')
        if anchor is not None and any(a <= anchor <= b for a, b in ranges):
            result.append(deepcopy(line))
    return result


def recipe_id(source_sha, recipe):
    # Source location, not title spelling or a mutable list ordinal, defines ID.
    for role in ('title', 'ingredients', 'method'):
        anchors = [(p['pdf_page'], a) for p in recipe['parts']
                   for a, _ in p.get(role, [])]
        if anchors:
            page, cell = min(anchors)
            key = f'{source_sha}:{page}:{cell}'
            return 'pdf_recipe_' + hashlib.sha256(key.encode()).hexdigest()[:24]
    raise ValueError('Recipe lacks a source anchor')


def assemble(source_sha, original_rows, ledger):
    rows = {r['pdf_page']: deepcopy(r) for r in original_rows}
    if len(rows) != len(original_rows):
        raise ValueError('Duplicate source page')
    resolutions = {r['issue_id']: r for r in ledger.get('owner_resolutions', [])}
    question_ids = {q.get('id') for q in ledger['questions']}
    if len(resolutions) != len(ledger.get('owner_resolutions', [])) or not set(resolutions) <= question_ids:
        raise ValueError('Duplicate or unknown owner resolution')
    corrections = defaultdict(list)
    for c in ledger['corrections']:
        corrections[c['pdf_page']].append(c)
    for n, row in rows.items():
        view = deepcopy(preferred_lines(row))
        seen = set()
        for c in corrections[n]:
            cell = c['cell_index']
            matches = [l for l in view if l['cell_index'] == cell]
            if cell in seen or len(matches) != 1 or matches[0]['text'] != c['before']:
                raise ValueError(f'Stale or duplicate correction: {n}:{cell}')
            seen.add(cell)
            matches[0]['text'] = c['after']
            matches[0]['recipe_review_correction'] = deepcopy(c)
        if str(n) in ledger['pages']:
            row['recipe_reviewed_lines'] = view
            row['recipe_reviewed_printed_page'] = ledger['pages'][str(n)]['printed_page']
        elif corrections[n]:
            raise ValueError(f'Correction without page review: {n}')
    owner_seen = set()
    for resolution in resolutions.values():
        for change in resolution.get('changes', []):
            n, cell = change['pdf_page'], change['cell_index']
            if str(n) not in ledger['pages'] or (n, cell) in owner_seen:
                raise ValueError('Duplicate or unreviewed owner correction')
            owner_seen.add((n, cell))
            row = rows[n]
            if 'recipe_owner_interpreted_lines' not in row:
                row['recipe_owner_interpreted_lines'] = deepcopy(row['recipe_reviewed_lines'])
            matches = [l for l in row['recipe_owner_interpreted_lines'] if l['cell_index'] == cell]
            if len(matches) != 1 or matches[0]['text'] != change['before']:
                raise ValueError(f'Stale owner correction: {n}:{cell}')
            matches[0].update(text=change['after'], owner_resolution_id=resolution['issue_id'],
                              owner_resolution_before=change['before'])
    by_key = {r['key']: r for r in ledger['recipes']}
    ids = {k: recipe_id(source_sha, r) for k, r in by_key.items()}
    if len(by_key) != len(ledger['recipes']) or len(set(ids.values())) != len(ids):
        raise ValueError('Duplicate recipe key or source anchor')
    records, usage = [], defaultdict(list)
    for spec in ledger['recipes']:
        key = spec['key']
        parent = spec.get('parent_key')
        if parent and parent not in ids:
            raise ValueError(f'Missing parent: {parent}')
        sections = {role: [] for role in ROLES}
        for p in spec['parts']:
            n = p['pdf_page']
            if str(n) not in ledger['pages']:
                raise ValueError(f'Unreviewed recipe page: {n}')
            for role in ROLES:
                ranges = p.get(role, [])
                lines = assembled_lines(rows[n])
                for a, b in ranges:
                    if a > b or not selected(lines, [[a, b]]):
                        raise ValueError(f'Empty range: {n}:{a}-{b}')
                for line in selected(lines, ranges):
                    line['printed_page'] = ledger['pages'][str(n)]['printed_page']
                    line['selected_layer'] = ('recipe_owner_interpreted_lines'
                        if 'owner_resolution_id' in line else next(k for k in LAYERS if k in rows[n]))
                    sections[role].append(line)
                    identity = (n, line['cell_index'], line.get('insert_after_original_cell'))
                    usage[identity].append((key, role))
        questions = [deepcopy(q) for q in ledger['questions']
                     if q['recipe_key'] == key and q.get('id') not in resolutions]
        resolved = [dict(question=deepcopy(q), resolution=deepcopy(resolutions[q['id']]))
                    for q in ledger['questions'] if q['recipe_key'] == key and q.get('id') in resolutions]
        records.append(dict(id=ids[key], source_sha256=source_sha,
            **deepcopy(spec), parent_id=ids.get(parent), sections=sections,
            source_locations=[dict(pdf_page=p['pdf_page'],
                printed_page=ledger['pages'][str(p['pdf_page'])]['printed_page'])
                for p in spec['parts']], questions=questions, resolved_source_questions=resolved,
            status='source_reviewed_with_uncertainty' if questions else 'source_reviewed_candidate',
            verified_complete_recipe=False, personally_tested=False))
    # Only source-shared methods/metadata and explicit parent/variant excerpts overlap.
    for (n, cell, anchor), uses in usage.items():
        if len(uses) < 2:
            continue
        for key, role in uses:
            others = [k for k, _ in uses if k != key]
            related = all(by_key[k].get('parent_key') == key or parent_of(by_key, key) == k
                          for k in others)
            shared = any(s['pdf_page'] == n and s['role'] == role
                         and s['cells'][0] <= (cell if cell is not None else anchor) <= s['cells'][1]
                         for s in ledger.get('shared_ranges', []))
            if not (related or shared):
                raise ValueError(f'Unexplained cross-recipe overlap: {n}:{cell} {uses}')
    contexts = []
    for spec in ledger.get('context', []):
        context = deepcopy(spec)
        context['recipe_ids'] = [ids[k] for k in spec['applies_to']]
        context['lines'] = selected(assembled_lines(rows[spec['pdf_page']]),
                                    spec['heading'] + spec['body'])
        contexts.append(context)
    for r in records:
        r['component_ids'] = [x['id'] for x in records if x['parent_id'] == r['id'] and x['kind'] == 'component']
        r['variant_ids'] = [x['id'] for x in records if x['parent_id'] == r['id'] and x['kind'] == 'variant']
        r['context_indices'] = [i for i, c in enumerate(contexts) if r['id'] in c['recipe_ids']]
        r['related_source_question_ids'] = [q['id'] for q in ledger['questions']
            if q.get('id') not in resolutions and (q['recipe_key'] == r.get('parent_key') or
            any(x['key'] == q['recipe_key'] and x['id'] in r['component_ids'] for x in records))]
        if r['related_source_question_ids']:
            r['status'] = 'source_reviewed_with_related_uncertainty'
    return dict(pages=list(rows.values()), recipes=records, contexts=contexts,
                counts=dict(Counter(r['kind'] for r in records)))


def parent_of(by_key, key):
    return by_key[key].get('parent_key')
