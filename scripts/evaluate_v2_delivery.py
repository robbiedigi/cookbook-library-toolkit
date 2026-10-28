"""Independent transport/source accounting at the compiled internal-record boundary.

This proves read DTO delivery against compiled clean source records, not the
canonical-to-database projection by itself. Canonical ledgers/source archives and
the separate converter source-parity audits establish that preceding boundary.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from library_v2.contracts import canonical_json, validate_output
from library_v2.service import SnapshotService
from library_v2.store import clean


def reconstruct(parts):
    """Independent header-once/range reconstruction; no production projection."""
    if not parts or [p['part_index'] for p in parts] != list(range(len(parts))):
        raise AssertionError('Missing, duplicate or unordered delivery part')
    if not parts[-1]['complete'] or any(p['complete'] for p in parts[:-1]):
        raise AssertionError('Incorrect record-complete flags')
    if 'content' in parts[0]:
        if len(parts)!=1:raise AssertionError('Whole content mixed with parts')
        return copy.deepcopy(parts[0]['content'])
    if 'header' not in parts[0] or any('header' in p for p in parts[1:]):
        raise AssertionError('Header must occur exactly once, first')
    content=copy.deepcopy(parts[0]['header']);blocks={};ends={};totals={}
    for part in parts[1:]:
        for segment in part['segments']:
            section,index=segment['section'],segment['index'];group=segment.get('group_index')
            key=(section,group,index);incoming=segment['value']
            if key in blocks and not ('text_range' in segment or 'row_range' in segment):
                raise AssertionError('Duplicated atomic source value')
            field='text' if 'text_range' in segment else 'rows' if 'row_range' in segment else None
            if key not in blocks:
                blocks[key]=copy.deepcopy(incoming)
                if field=='text':blocks[key]='' if isinstance(incoming,str) else dict(blocks[key],text='')
                if field=='rows':blocks[key]=dict(blocks[key],rows=[])
            elif isinstance(incoming,dict):
                if not isinstance(blocks[key],dict) or any(k!=field and blocks[key].get(k)!=v for k,v in incoming.items()):
                    raise AssertionError('Changed continuation value metadata')
            if field:
                span=segment['text_range' if field=='text' else 'row_range'];total=span['total_characters' if field=='text' else 'total_rows']
                value=incoming if isinstance(incoming,str) else incoming[field]
                range_key=(key,field)
                if span['start']!=ends.get(range_key,0) or span['end']-span['start']!=len(value):
                    raise AssertionError('Gap, overlap or invalid continuation range')
                if range_key in totals and totals[range_key]!=total:raise AssertionError('Changed range total')
                totals[range_key],ends[range_key]=total,span['end']
                if isinstance(blocks[key],str):blocks[key]+=value
                else:blocks[key][field]+=value
    if any(ends[key]!=total for key,total in totals.items()):raise AssertionError('Truncated source value')
    for (section,group,index),value in blocks.items():
        if section=='ingredients':target=content['ingredients'][group]['items']
        elif section=='menu_dishes':target=content['menu']['dishes']
        else:target=content.setdefault(section,[])
        if index!=len(target):raise AssertionError('Source value ordering changed')
        target.append(value)
    return content


def assert_source_equivalence(delivered, original):
    """Account original source data independently of reader.cooking_item.

    Exact generic audit dispositions are declared contract data; unknown source
    qualifications must survive. This does not call the implementation projector.
    """
    policy=json.loads((ROOT/'library_v2/schemas/reader-policy.json').read_text())
    for key in ('id','entity','title'):
        if delivered[key]!=original[key]:raise AssertionError('Changed source identity/title')
    def locator(c):
        if c['printed_labels']:kind,labels='printed_pages',c['printed_labels']
        elif c['pdf_file_pages']:kind,labels='pdf_file_pages',c['pdf_file_pages']
        else:kind,labels='page_unknown',True
        return dict(edition_id=c['edition_id'],book=c['book'],**{kind:labels})
    citations={c['id']:locator(c) for c in original['citations']}
    if {canonical_json(source) for source in delivered['sources']}!={canonical_json(source) for source in citations.values()}:
        raise AssertionError('Missing, invented or relabelled source page')
    default=delivered.get('source_refs',[0] if len(delivered['sources'])==1 else [])
    def blocks(expected,actual,section):
        if len(expected)!=len(actual):raise AssertionError('Dropped or repeated source block: '+section)
        for old,new in zip(expected,actual):
            text=new if isinstance(new,str) else new['text']
            if text!=old['text']:raise AssertionError('Changed source text: '+section)
            if old['kind']!='text':
                if not isinstance(new,dict) or new['kind']!=old['kind']:raise AssertionError('Changed source block type')
                for field in ('rows','header_rows') if old['kind']=='table' else ('asset_refs',):
                    if old[field]!=new[field]:raise AssertionError('Changed table/figure data: '+field)
            new_basis=None if isinstance(new,str) else new.get('basis')
            if (old['basis']=='generated_inference')!=(new_basis=='generated_inference'):
                raise AssertionError('Changed generated inference distinction')
            default_role=dict(ingredients='ingredient',method='method',headnotes='headnote',notes='note',additional_context='context',blocks='prose')[section]
            role=default_role if isinstance(new,str) else new.get('role',default_role)
            if role!=old['role']:
                raise AssertionError('Changed source heading or block role')
            refs=default if isinstance(new,str) else new.get('source_refs',default)
            actual_locators=[delivered['sources'][i] for i in refs]
            expected_locators=[citations[cid] for cid in old.get('citation_ids',[])]
            if {canonical_json(v) for v in actual_locators}!={canonical_json(v) for v in expected_locators}:
                raise AssertionError('Changed source book/printed/file-page attribution')
            if old.get('applicability'):
                aliases=policy['applicability_aliases'];value=aliases.get(old['applicability'],old['applicability'])
                scopes=delivered.get('applicability',{}).get(section,[])
                selected=0 if isinstance(new,str) else new.get('applicability_ref',0)
                if not scopes or scopes[selected]!=value:raise AssertionError('Changed material applicability')
            elif delivered.get('applicability',{}).get(section) and (isinstance(new,str) or new.get('applicability_ref','inherited') is not None):
                raise AssertionError('Inherited another block applicability scope')
    if original['entity']=='recipe':
        if len(delivered['ingredients'])!=len(original['ingredient_groups']):raise AssertionError('Changed ingredient grouping')
        for old,new in zip(original['ingredient_groups'],delivered['ingredients']):
            if old['title']!=new['title']:raise AssertionError('Changed ingredient heading')
            blocks(old['blocks'],new['items'],'ingredients')
        for old,new in [('method_blocks','method'),('headnotes','headnotes'),('notes','notes'),('additional_context','additional_context')]:
            expected=[b for b in original[old] if old!='notes' or b['role']!='context']
            blocks(expected,delivered.get(new,[]),new)
        if delivered.get('yield')!=original['yield']['text']:raise AssertionError('Changed yield')
        if (original['yield']['basis']=='generated_inference')!=(delivered.get('yield_basis')=='generated_inference'):
            raise AssertionError('Changed generated yield basis')
        if delivered.get('context_links',[])!=original.get('context_links',[]):raise AssertionError('Changed context pointers')
    else:
        if delivered['kind']!=original['kind']:raise AssertionError('Changed knowledge kind')
        blocks(original['blocks'],delivered['blocks'],'blocks')
        if 'menu' in original:
            old=original['menu'];new=delivered['menu'];indexes={b['id']:i for i,b in enumerate(original['blocks'])}
            for field in ('occasion','guest_count_text'):
                if old[field]!=new.get(field):raise AssertionError('Changed source menu metadata')
            if len(old['dishes'])!=len(new['dishes']):raise AssertionError('Changed menu dish count')
            for a,b in zip(old['dishes'],new['dishes']):
                for field in ('text','recipe_id','role','multiplier_text','alternative_group'):
                    if a[field]!=b.get(field):raise AssertionError('Changed menu dish/link/alternative')
                if b['block_indices']!=[indexes[key] for key in a['source_block_ids']]:raise AssertionError('Changed menu evidence links')
            if new['preparation_note_indices']!=[indexes[key] for key in old['preparation_note_block_ids']]:raise AssertionError('Changed menu preparation links')
    expected=[]
    for q in original['uncertainties']:
        if dict(code=q['code'],message=q['message']) in policy['archive_only_qualifications']:continue
        value={key:q[key] for key in ('code','message','affects','action')}
        if value not in expected:expected.append(value)
    if delivered.get('qualifications',[])!=expected:raise AssertionError('Dropped/changed unknown or material qualification')
    coverage=delivered['dependency_coverage'];old=original['dependency_coverage']
    if coverage['status']!=old['status']:raise AssertionError('Changed dependency coverage status')
    if old['scope'] not in policy['generic_coverage_scopes'] and coverage.get('scope')!=old['scope']:
        raise AssertionError('Dropped specific dependency scope')
    limits=[v for v in old['limitations'] if v not in policy['generic_coverage_limitations']]
    if coverage.get('limitations',[])!=limits:raise AssertionError('Dropped specific dependency limitation')
    archived=sum(dict(code=q['code'],message=q['message']) in policy['archive_only_qualifications'] for q in original['uncertainties'])
    return dict(raw_qualifications=len(original['uncertainties']),archive_only_exact_generic=archived,
        retained_material=len(expected),raw_qualifications_sha256=hashlib.sha256(canonical_json(original['uncertainties']).encode()).hexdigest())


def collect(service, tool, arguments):
    collected, pages, bytes_total = {}, 0, 0
    cursor = None
    cursors = set()
    roots, relationships, errors = [], [], []
    while True:
        response = getattr(service, tool)(**arguments, cursor=cursor)
        validate_output(response)
        pages += 1
        bytes_total += len(canonical_json(response).encode())
        roots += response["roots"]
        relationships += response["relationships"]
        errors += response["errors"]
        for item in response["items"]:
            key = (item["node"]["entity"], item["node"]["id"])
            collected.setdefault(key, []).append(item)
        cursor = response["next_cursor"]
        if not cursor:
            if not response["complete"]:
                raise AssertionError("Delivery ended without completeness: " + canonical_json(errors))
            break
        if cursor in cursors or pages > 10000:
            raise AssertionError("Continuation did not advance")
        cursors.add(cursor)
    records = {key: reconstruct(parts) for key, parts in collected.items()}
    qualification_audit={}
    for key, value in records.items():
        qualification_audit[key[0]+':'+key[1]]=assert_source_equivalence(value, service.snapshot.get(*key))
    return records, {"pages": pages, "bytes": bytes_total, "records": len(records), "errors": errors,
                     "roots": roots, "relationships": relationships,"qualification_audit":qualification_audit}




