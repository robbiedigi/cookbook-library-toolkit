"""Assemble edition-profiled native PDF candidates; never changes the live library."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cookbook.pdf_native import assemble, link_lines, apply_owner_decisions


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def run(capture, profile_path, output, owner_decisions=None):
    if output.exists():
        raise ValueError('Use a new output directory')
    manifest=json.loads((capture/'manifest.json').read_text())
    if hashlib.sha256(Path(manifest['file']).read_bytes()).hexdigest()!=manifest['sha256']:
        raise ValueError('Source file changed')
    pages=[json.loads(l) for l in (capture/'pages.jsonl').read_text().splitlines()]
    outlines=json.loads((capture/'outline.json').read_text())
    profile=json.loads(profile_path.read_text())
    norm=lambda s: re.sub(r'\s+','',s)
    mismatches=[p['pdf_page'] for p in pages if norm(p['pypdf_text'])!=norm('\n'.join(l['text'] for l in p['lines']))]
    if mismatches:
        raise ValueError(f'Independent text readers disagree: {mismatches}')
    records,contexts,report=assemble(pages,outlines,manifest,profile)
    ledger = json.loads(owner_decisions.read_text()) if owner_decisions else None
    if ledger:
        records = apply_owner_decisions(records, ledger)
        report['owner_decisions'] = dict(ids=[d['id'] for d in ledger['decisions']],
            sha256=hashlib.sha256(owner_decisions.read_bytes()).hexdigest())
    entries=[]
    # Capture the complete index, including non-recipe topics and unlinked headings.
    index_start=next(n['pdf_page'] for n in outlines if n['title'].casefold()=='index')
    for page in pages[index_start-1:]:
        if page['pdf_page']>index_start and not page['lines']:
            break
        for line in page['lines']:
            links=[dict(link, source_annotation=[page['pdf_page'], i])
                for i,link in enumerate(page['links']) if line['line_index'] in link_lines(page,link)]
            entries.append(dict(id=f"native-index-{manifest['book_id']}-{page['pdf_page']}-{line['line_index']}",
                label=line['text'],source_text=line['text'],hierarchy=[line['text']],
                source=dict(book_id=manifest['book_id'],pdf_page=page['pdf_page'],printed_page=None,line_index=line['line_index']),
                links=links, source_line=line,
                hierarchy_status='flat converted layout; contextual reconstruction required'))
    output.mkdir(parents=True)
    if ledger:
        write(output/'owner-decisions.json',ledger)
    (output/'recipes.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
    write(output/'contexts.json',contexts)
    write(output/'index-capture.json',dict(source=manifest,entries=entries))
    report.update(pages_captured=len(pages), independent_text_agreement_pages=len(pages),
        index_entries=len(entries),index_annotations=len({tuple(l['source_annotation']) for e in entries for l in e['links']}),
        profile_sha256=hashlib.sha256(profile_path.read_bytes()).hexdigest(),
        input_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in capture.iterdir() if p.is_file()})
    write(output/'assembly-report.json',report)
    cards=output/'cards';cards.mkdir()
    for r in records:
        text=f"# {r['title']}\n\nID: {r['id']}\nKind: {r['record_kind']}\nPDF pages: {r['pdf_pages']}\nPrinted labels: unavailable\n\n"
        text+='\n'.join('> '+w for w in r['warnings'])+'\n\n'
        for b in r['blocks']:
            text+=f"## {b['kind']} — PDF {b['pdf_pages']}\n\n{b['text']}\n\n"
        (cards/f"{r['id']}.md").write_text(text)
    print(json.dumps({k:v for k,v in report.items() if k!='coverage'},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('capture','profile','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--owner-decisions',type=Path)
    a=parser.parse_args();run(a.capture,a.profile,a.output,a.owner_decisions)
