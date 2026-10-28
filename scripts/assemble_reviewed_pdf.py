#!/usr/bin/env python3
"""Build a private review bundle; deliberately has no live promotion operation."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cookbook.pdf_review_assembly import assemble, assembled_lines


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-bundle', required=True, type=Path)
    ap.add_argument('--ledger', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    args = ap.parse_args()
    ledger = json.loads(args.ledger.read_text())
    base = args.source_bundle
    index = json.loads((base / 'book-index.json').read_text())
    source_sha = index['source']['sha256']
    if ledger['source_sha256'] != source_sha:
        raise ValueError('Review ledger belongs to a different source')
    for reviewed in ledger['pages'].values():
        render = Path(reviewed['source_render'])
        if sha(render) != reviewed['source_render_sha256']:
            raise ValueError(f'Source render changed: {render}')
    rows = [json.loads(s) for s in (base / 'page-evidence.jsonl').read_text().splitlines()]
    result = assemble(source_sha, rows, ledger)
    # Account for every selected-view line on every recipe page. Supporting
    # essays and raw pages remain available without asserting transcription QA.
    recipe_pages = {p['pdf_page'] for r in ledger['recipes'] for p in r['parts']}
    coverage = []
    for row in result['pages']:
        n = row['pdf_page']
        lines = assembled_lines(row)
        assignments = []
        for line in lines:
            def identity(l):
                return (l['cell_index'], l.get('insert_after_original_cell'), l['text'])
            hits = [dict(recipe_id=r['id'], role=role) for r in result['recipes']
                    for role, texts in r['sections'].items()
                    if any(l['pdf_page'] == n and identity(l) == identity(line) for l in texts)]
            context = [i for i, c in enumerate(result['contexts'])
                       if c['pdf_page'] == n and any(identity(l) == identity(line) for l in c['lines'])]
            excluded = [e['reason'] for e in ledger.get('non_recipe_cells', [])
                        if e['pdf_page'] == n and e['cell_index'] == line['cell_index']]
            if n in recipe_pages and not (hits or context or excluded):
                raise ValueError(f'Unassigned recipe-page line: {n}:{line["cell_index"]}')
            assignments.append(dict(cell_index=line['cell_index'],
                insert_after_original_cell=line.get('insert_after_original_cell'),
                recipes=hits, context_indices=context, exclusions=excluded))
        coverage.append(dict(pdf_page=n, recipe_page=n in recipe_pages,
            supporting_page=ledger.get('supporting_pages', {}).get(str(n)), lines=assignments))
    out = args.output
    if out.exists() and any(out.iterdir()):
        raise ValueError('Use a fresh output directory to preserve earlier evidence')
    out.mkdir(parents=True, exist_ok=True)
    def dump(name, obj):
        (out / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    for name, field in [('page-evidence.jsonl', 'pages'), ('recipes.jsonl', 'recipes')]:
        (out / name).write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in result[field]))
    for filename in ('book-index.json', 'raw-blocks.jsonl'):
        shutil.copyfile(base / filename, out / filename)
    dump('review-ledger.json', ledger)
    dump('recipe-contexts.json', result['contexts'])
    dump('coverage.json', coverage)
    for r in result['recipes']:
        path = out / 'markdown' / (r['id'] + '.md')
        path.parent.mkdir(exist_ok=True)
        parts = [f'# {r["title"]}', f'Status: {r["status"]}; private candidate, not personally tested.',
                 f'Source ID: {r["id"]}', f'Locations: {json.dumps(r["source_locations"])}']
        if r['note']:
            parts.append('Review note: ' + r['note'])
        if r['parent_id']:
            parts.append('Parent recipe: ' + r['parent_id'])
        if r['component_ids']:
            parts.append('Linked components: ' + ', '.join(r['component_ids']))
        for i in r['context_indices']:
            parts += ['## Shared source context', '\n'.join(l['text'] for l in result['contexts'][i]['lines'])]
        for role, texts in r['sections'].items():
            if texts:
                parts += [f'## {role.title()}', '\n'.join(l['text'] for l in texts)]
        if r['questions']:
            parts += ['## Unresolved source questions', '\n'.join(q['id'] + ': ' + q['issue'] for q in r['questions'])]
        if r['resolved_source_questions']:
            parts += ['## Owner decisions', '\n'.join(x['resolution']['issue_id'] + ': ' +
                x['resolution']['decision'] for x in r['resolved_source_questions'])]
        if r['related_source_question_ids']:
            parts += ['Related parent/component questions: ' + ', '.join(r['related_source_question_ids'])]
        path.write_text('\n\n'.join(parts) + '\n')
    report = dict(source_sha256=source_sha, source_bundle=str(base),
        input_hashes={p.name: sha(p) for p in base.iterdir() if p.is_file()},
        ledger_sha256=sha(args.ledger), page_count=len(rows),
        recipe_page_count=len(recipe_pages), reviewed_page_count=len(ledger['pages']),
        counts=result['counts'], corrections=len(ledger['corrections']),
        questions=sum(len(r['questions']) for r in result['recipes']),
        resolved_questions=len(ledger.get('owner_resolutions', [])),
        original_fields_preserved=True,
        raw_blocks_preserved=sha(base/'raw-blocks.jsonl') == sha(out/'raw-blocks.jsonl'),
        raw_index_preserved=sha(base/'book-index.json') == sha(out/'book-index.json'),
        live_promoted=False)
    assert all(all(after[k] == v for k, v in before.items())
               for before, after in zip(rows, result['pages']))
    dump('assembly-report.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
