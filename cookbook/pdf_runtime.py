"""Project explicitly reviewed PDF records into the existing library contract.

This does not extract PDFs or certify printed quantities. Review IDs, source
lines, owner interpretations and linked variations remain distinguishable.
"""
from copy import deepcopy
import hashlib
import re

VERSION = 'reviewed-pdf-runtime-1'
ROLE_KIND = dict(title='title', metadata='metadata', headnote='headnote',
                 ingredients='ingredient', method='instruction', notes='note')


def runtime_id(review_id):
    return hashlib.sha256(('reviewed-pdf:' + review_id).encode()).hexdigest()[:16]


def context_sections(contexts, source):
    """Keep all assembled surrounding prose, including context with no recipe link."""
    sections = []
    for idx, context in enumerate(contexts):
        lines = context['lines']
        text = '\n'.join(line['text'] for line in lines)
        if not text.strip():
            continue
        heading = ' '.join(line['text'] for line in lines
            if any(a <= line['cell_index'] <= b for a, b in context.get('heading', [])))
        title = heading or f"Source context — PDF page {context['pdf_page']}"
        loc = dict(book_id=source['book_id'], book=source['title'], author=source['author'],
                   file=source['file'], sha256=source['sha256'], format='pdf', extractor=VERSION,
                   member=None, anchor=None, pdf_page=context['pdf_page'],
                   printed_page=next((line.get('printed_page') for line in lines
                                      if line.get('printed_page') is not None), None))
        sid = 's_' + hashlib.sha256((source['sha256'] + '|context|' + str(idx)).encode()).hexdigest()[:16]
        sections.append(dict(id=sid, title=title, headings=[source['title'], title], source=loc,
            blocks=[dict(kind='text', text=text, source=deepcopy(loc), source_lines=deepcopy(lines))],
            flags=['Assembled PDF source context; not a verified complete recipe; consult retained source lines and review qualifications.']))
    return sections


def qualify_index_links(bundle, records):
    """Require direct source-list evidence; avoid paragraph-wide role inference.

    A role word elsewhere in a PDF excerpt does not qualify each ingredient.
    The three banana-leaf relationships have separate source-reviewed roles.
    """
    by_id={r['id']:r for r in records}
    for row in bundle['relationships']:
        resolution=row['recipe_resolution']; rid=resolution.get('recipe_id')
        if not rid: continue
        recipe=by_id[rid]
        if resolution.get('correction'):
            row['runtime_review']=dict(original_resolution=deepcopy(resolution),
                reason='Locator disagreement requires explicit destination review before PDF promotion.')
            row['recipe_resolution']=dict(status='unresolved',recipe_id=None,component_id=None,
                candidate_ids=[rid],evidence=resolution['evidence'],correction=resolution['correction'])
            row['ingredient_validation']=dict(status='unresolved_recipe',roles=[],evidence=[])
            continue
        validation=row['ingredient_validation']
        if validation['status']!='corroborated': continue
        original=deepcopy(validation)
        direct=[e for e in validation['evidence'] if e['kind']=='ingredient' and e['corroborates']]
        if not direct:
            validation.update(status='unresolved_extraction_review',roles=[],
                review_reason='Method-only PDF association needs contextual review; no direct ingredient-list evidence.')
        else:
            validation.update(roles=['source_listed_usage_unclassified'],
                evidence=[dict(e,role='unclassified',optional=None,alternative=None) for e in direct],
                review_reason='Ingredient is source-listed. Read the full excerpt for alternatives and optional use; paragraph-wide role inference is not certified.')
        if row['ingredient']=='banana leaf' and recipe['review_key'] in {
                'baked-fish-with-greens','basic-tamal','broccoli-tamales'}:
            alternative=recipe['review_key']=='basic-tamal'
            validation.update(status='corroborated',roles=['wrapper','alternative'] if alternative else ['wrapper'],
                evidence=[dict(e,role='wrapper',optional=False,alternative=alternative) for e in direct],
                review_reason='Source-reviewed banana-leaf wrapper relationship; Basic Tamal explicitly allows corn husks instead.')
        row['runtime_review']=dict(original_validation=original,
            reason='Separate source-listed membership from unreviewed qualification; retain original automated evidence.')
    from collections import Counter
    bundle['report']['recipe_resolution']=dict(Counter(r['recipe_resolution']['status'] for r in bundle['relationships']))
    bundle['report']['ingredient_validation']=dict(Counter(r['ingredient_validation']['status'] for r in bundle['relationships']))
    return bundle


def project(records, contexts, pages, source):
    """Keep wrapped lines in source order within each reviewed role/page group.

    Ingredient groups are source excerpts, not inferred individual ingredient
    quantities. Variants retain their changes and a separately labelled base.
    """
    by_page = {p['pdf_page']: p for p in pages}
    ids = {r['id']: runtime_id(r['id']) for r in records}
    if len(set(ids.values())) != len(records):
        raise ValueError('Duplicate runtime identity')
    result = []
    for r in records:
        if r['questions'] or r['related_source_question_ids']:
            raise ValueError('Unresolved review decision: ' + r['key'])
        locations = deepcopy(r['source_locations'])
        loc = locations[0]
        provenance = dict(file=source['file'], sha256=source['sha256'], format='pdf',
            member=f"pdf/page/{loc['pdf_page']}", anchor=r['id'],
            pdf_page=loc['pdf_page'], printed_page=loc['printed_page'],
            locations=locations, extractor=VERSION)
        blocks, original_lines, normalizations = [], [], []
        for role, lines in r['sections'].items():
            groups = []
            for line in lines:
                if not groups or groups[-1][0]['pdf_page'] != line['pdf_page']:
                    groups.append([])
                groups[-1].append(line)
            for group in groups:
                originals = []
                for line in group:
                    matches = [x for x in by_page[line['pdf_page']]['review_lines']
                               if x['cell_index'] == line['cell_index']
                               and x.get('insert_after_original_cell') == line.get('insert_after_original_cell')]
                    original = matches[0]['text'] if len(matches) == 1 else None
                    originals.append(dict(pdf_page=line['pdf_page'], cell_index=line['cell_index'], text=original))
                    if original != line['text']:
                        normalizations.append(dict(block_index=len(blocks),
                            pdf_page=line['pdf_page'], cell_index=line['cell_index'],
                            before=original, after=line['text'],
                            kind='owner_interpretation' if line.get('owner_resolution_id') or line.get('owner_interpretation_issue_id') else 'reviewed_source_correction',
                            evidence=deepcopy(line)))
                block = dict(kind=ROLE_KIND[role], text='\n'.join(x['text'] for x in group),
                    tag='pdf_excerpt', classes=[], path=f"pdf/page/{group[0]['pdf_page']}",
                    anchors=[f"cell-{x['cell_index']}" for x in group],
                    pages=sorted({x['printed_page'] for x in group}), links=[],
                    pdf_pages=[group[0]['pdf_page']], source_lines=deepcopy(group), original_lines=originals)
                blocks.append(block)
                original_lines.extend(x['text'] for x in originals if x['text'] is not None)
        warnings = [r['note']] if r.get('note') else []
        if r['kind'] == 'variant':
            warnings.append('Variation instructions only; use the separately labelled parent recipe and its source limitations. Base ingredients are not silently inherited.')
        record = dict(id=ids[r['id']], review_id=r['id'], review_key=r['key'],
            book_id=source['book_id'], book=source['title'], author=source['author'],
            title=r['title'], aliases=r['aliases'], record_kind=r['kind'],
            ingredients=[b['text'] for b in blocks if b['kind']=='ingredient'],
            instructions=[b['text'] for b in blocks if b['kind']=='instruction'],
            headnote='\n'.join(b['text'] for b in blocks if b['kind']=='headnote'),
            **{'yield':next((l['text'] for l in r['sections']['metadata'] if re.match(r'(?i)(serves|makes|yields)\b',l['text'])),None)},
            blocks=blocks, source_text='\n'.join(original_lines), warnings=warnings,
            normalizations=normalizations, source=provenance, references=[], supporting_notes=[],
            parent_id=ids.get(r['parent_id']), component_ids=[ids[x] for x in r['component_ids']],
            variant_ids=[ids[x] for x in r['variant_ids']],
            printed_pages=[l['printed_page'] for l in locations], pdf_pages=[l['pdf_page'] for l in locations],
            review=dict(status='source_reviewed_with_retained_limitations',
                verified_complete_recipe=False, personally_tested=None,
                note=r.get('note'), resolved_source_questions=deepcopy(r['resolved_source_questions']),
                source_sections=deepcopy(r['sections']), ingredient_representation='reviewed source excerpts; not parsed quantity objects'))
        result.append(record)
    by_id = {r['id']:r for r in result}
    for r, original in zip(result, records):
        relations = [('component', rid) for rid in r['component_ids']]
        relations += [('variation', rid) for rid in r['variant_ids']]
        if r['parent_id']:
            relations.append(('base_recipe' if r['record_kind']=='variant' else 'used_by', r['parent_id']))
        for kind, rid in relations:
            target = by_id[rid]
            r['references'].append(dict(kind=kind, recipe_id=rid, title=target['title'], source=deepcopy(target['source'])))
            if kind == 'used_by':
                continue
            r['supporting_notes'].append(dict(id=rid, book_id=r['book_id'],
                title=target['title'], relationship=kind, path=target['source']['member'],
                source=deepcopy(target['source']), blocks=deepcopy(target['blocks']),
                warnings=deepcopy(target['warnings']), review_id=target['review_id']))
        for idx in original['context_indices']:
            c=contexts[idx]
            heading=' '.join(l['text'] for l in c['lines']
                if any(a <= l['cell_index'] <= b for a,b in c['heading']))
            r['supporting_notes'].append(dict(id=f"pdf-context-{idx}", book_id=r['book_id'],
                title=heading, relationship='source_context', path=f"pdf/page/{c['pdf_page']}",
                source=dict(provenance_kind='source_context',pdf_page=c['pdf_page']),
                blocks=[dict(kind='note',text='\n'.join(l['text'] for l in c['lines']), source_lines=deepcopy(c['lines']))]))
    return result
