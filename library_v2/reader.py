"""Compact cooking evidence and lossless bounded delivery, independent of audit IDs.

Canonical archives retain original source and editorial/review basis. Strings
are accepted cooking text; generated inference and exceptional page scope remain
explicit. Continuations inherit block metadata
from their first range. Neither projection nor transport invents cooking content.
"""
from copy import deepcopy
from collections import Counter
from functools import lru_cache
import json
from pathlib import Path
from .contracts import canonical_json

READER_VERSION = 'library-v2-reader-2'
PART_BYTES = 112 * 1024


class ReaderError(ValueError):
    pass


@lru_cache(maxsize=1)
def archive_policy():
    return json.loads((Path(__file__).with_name('schemas')/'reader-policy.json').read_text())


def _encoded(value):
    return canonical_json(value).encode()


def source_reference(citation):
    result = dict(edition_id=citation['edition_id'], book=citation['book'])
    if citation.get('document'):
        result['document'] = deepcopy(citation['document'])
        if citation.get('author'):
            result['author'] = citation['author']
    elif citation['printed_labels']:
        result['printed_pages'] = citation['printed_labels']
    elif citation['pdf_file_pages']:
        # File indices are never presented as printed labels.
        result['pdf_file_pages'] = citation['pdf_file_pages']
    else:
        result['page_unknown'] = True
    return result


def cooking_item(item):
    """Project canonical or internal clean records into the production read DTO."""
    policy = archive_policy()
    sources, citation_refs = [], {}
    for citation in item['citations']:
        ref = source_reference(citation)
        if ref not in sources:
            sources.append(ref)
        citation_refs[citation['id']] = sources.index(ref)

    def refs(block):
        ids = block.get('citation_ids')
        if ids is None:
            ids = [span['citation_id'] for span in block.get('source_spans', [])]
        return sorted({citation_refs[c] for c in ids})

    raw_sections = {}
    if item['entity'] == 'recipe':
        raw_sections['ingredients'] = [b for g in item['ingredient_groups'] for b in g['blocks']]
        raw_sections.update(method=item['method_blocks'], headnotes=item['headnotes'],
            notes=[b for b in item['notes'] if b['role'] != 'context'], additional_context=item['additional_context'])
    else:
        raw_sections['blocks'] = item['blocks']
    all_blocks = [b for blocks in raw_sections.values() for b in blocks]
    cited_ids = {c for b in all_blocks for c in (b.get('citation_ids') if 'citation_ids' in b else [s['citation_id'] for s in b.get('source_spans', [])])}
    if item['entity'] == 'recipe':
        # General attached chapter notes are independently readable knowledge.
        kept = [c for c in item['citations'] if c['location_scope']=='item' or c['id'] in cited_ids]
        used = {citation_refs[c['id']] for c in kept}
        remap = {old:new for new,old in enumerate(sorted(used))}
        sources = [sources[i] for i in sorted(used)]
        citation_refs = {cid:remap[index] for cid,index in citation_refs.items() if index in remap}
    ref_sets = [refs(b) for b in all_blocks]
    default_refs = list(Counter(tuple(value) for value in ref_sets).most_common(1)[0][0]) if ref_sets else list(range(len(sources)))
    coverage = dict(status=item['dependency_coverage']['status'])
    if item['dependency_coverage']['scope'] not in policy['generic_coverage_scopes']:
        coverage['scope'] = item['dependency_coverage']['scope']
    limits = [v for v in item['dependency_coverage']['limitations'] if v not in policy['generic_coverage_limitations']]
    if limits:
        coverage['limitations'] = limits
    result = dict(entity=item['entity'], id=item['id'], title=item['title'], sources=sources,
                  dependency_coverage=coverage)
    if default_refs != ([0] if len(sources)==1 else []):
        result['source_refs'] = default_refs
    applicability = {}
    defaults = dict(ingredients='ingredient', method='method', headnotes='headnote', notes='note', additional_context='context', blocks='prose')
    for section, blocks in raw_sections.items():
        values = list(dict.fromkeys(b['applicability'] for b in blocks if b.get('applicability')))
        if values:
            applicability[section] = [policy['applicability_aliases'].get(v,v) for v in values]
    if applicability:
        result['applicability'] = applicability

    def block_value(block, section):
        extra = {}
        block_refs = refs(block)
        if block_refs != default_refs:
            extra['source_refs'] = block_refs
        if block['basis'] == 'generated_inference':
            extra['basis'] = block['basis']
        if block['role'] != defaults[section]:
            extra['role'] = block['role']
        if section in applicability:
            if not block.get('applicability'):
                extra['applicability_ref'] = None
            elif len(applicability[section]) > 1:
                extra['applicability_ref'] = applicability[section].index(policy['applicability_aliases'].get(block['applicability'],block['applicability']))
        if block['kind'] == 'text':
            return dict(text=block['text'], **extra) if extra else block['text']
        value = dict(kind=block['kind'], text=block['text'], **extra)
        if block['kind']=='table':
            value.update(rows=deepcopy(block['rows']),header_rows=list(block['header_rows']))
        else:
            value.update(asset_refs=list(block['asset_refs']),visual_content='not_transcribed')
        return value

    if item['entity'] == 'recipe':
        result['ingredients'] = [dict(title=g['title'],items=[block_value(b,'ingredients') for b in g['blocks']]) for g in item['ingredient_groups']]
        result['method'] = [block_value(b,'method') for b in raw_sections['method']]
        for section in ('headnotes','notes','additional_context'):
            if raw_sections[section]:
                result[section] = [block_value(b,section) for b in raw_sections[section]]
        if item['yield']['text'] is not None:
            result['yield'] = item['yield']['text']
            if item['yield']['basis']=='generated_inference':
                result['yield_basis'] = item['yield']['basis']
        if item.get('context_links'):
            result['context_links'] = deepcopy(item['context_links'])
    else:
        result['kind'] = item['kind']
        result['blocks'] = [block_value(b,'blocks') for b in raw_sections['blocks']]
        if 'menu' in item:
            indexes = {b['id']:i for i,b in enumerate(item['blocks'])}
            menu = item['menu']
            compact = {k:menu[k] for k in ('occasion','guest_count_text') if menu[k] is not None}
            compact['dishes'] = []
            for dish in menu['dishes']:
                value = {k:dish[k] for k in ('text','recipe_id','role','multiplier_text','alternative_group') if dish[k] is not None}
                value['block_indices'] = [indexes[key] for key in dish['source_block_ids'] if key in indexes]
                missing = [key for key in dish['source_block_ids'] if key not in indexes]
                if missing:
                    raise ReaderError('Menu dish points outside retained source blocks: ' + item['id'])
                compact['dishes'].append(value)
            compact['preparation_note_indices'] = [indexes[key] for key in menu['preparation_note_block_ids'] if key in indexes]
            if len(compact['preparation_note_indices'])!=len(menu['preparation_note_block_ids']):
                raise ReaderError('Menu preparation note points outside retained source blocks: ' + item['id'])
            result['menu'] = compact
    qualifications = []
    for uncertainty in item['uncertainties']:
        if dict(code=uncertainty['code'],message=uncertainty['message']) in policy['archive_only_qualifications']:
            continue
        value = {key:deepcopy(uncertainty[key]) for key in ('code','message','affects','action')}
        if value not in qualifications:
            qualifications.append(value)
    if qualifications:
        result['qualifications'] = qualifications
    return result


def delivery_parts(item, part_bytes=PART_BYTES):
    """Whole normal items; oversized metadata once, then indexed typed parts."""
    content = cooking_item(item)
    common = dict(node=dict(entity=item['entity'],id=item['id']))
    whole = dict(common,part_index=0,complete=True,content=content)
    if len(_encoded(whole)) <= part_bytes:
        yield whole
        return
    sections = ('method','headnotes','notes','additional_context') if item['entity']=='recipe' else ('blocks',)
    header = {k:deepcopy(v) for k,v in content.items() if k not in sections}
    header['method' if item['entity']=='recipe' else 'blocks'] = []
    if 'ingredients' in header:
        header['ingredients'] = [dict(title=g['title'],items=[]) for g in content['ingredients']]
    if 'menu' in header:
        header['menu']['dishes'] = []
    first = dict(common,part_index=0,complete=False,header=header)
    if len(_encoded(first)) > part_bytes:
        raise ReaderError('Oversized delivery header: ' + item['id'])
    yield first
    segments = []
    if item['entity']=='recipe':
        for group_index,group in enumerate(content['ingredients']):
            for index,value in enumerate(group['items']):
                segments.extend(_value_segments(dict(section='ingredients',group_index=group_index,index=index),value,part_bytes-1024))
    for section in sections:
        for index,value in enumerate(content.get(section,[])):
            segments.extend(_value_segments(dict(section=section,index=index),value,part_bytes-1024))
    for index,value in enumerate(content.get('menu',{}).get('dishes',[])):
        segments.extend(_value_segments(dict(section='menu_dishes',index=index),value,part_bytes-1024))
    if not segments:
        raise ReaderError('Oversized content without splittable fields: ' + item['id'])
    # Bounded batches, each segment encoded once for linear accounting.
    batch=[];size=0;ordinal=1
    for segment in segments:
        encoded_size=len(_encoded(segment))
        if batch and size+encoded_size+1 > part_bytes-1024:
            yield dict(common,part_index=ordinal,complete=False,segments=batch)
            ordinal+=1;batch=[];size=0
        batch.append(segment);size+=encoded_size+(1 if size else 0)
    yield dict(common,part_index=ordinal,complete=True,segments=batch)


def _value_segments(base,value,budget):
    if len(_encoded(dict(base,value=value)))<=budget:
        return [dict(base,value=value)]
    if isinstance(value,str) or isinstance(value,dict) and value.get('kind','text')=='text' and 'text' in value:
        return _text_segments(base,value,budget)
    if isinstance(value,dict) and value.get('kind')=='table':
        # Caption and rows are independent ranged fields of one table value.
        # Emit caption+metadata even for zero rows; metadata is never omitted
        # merely because the row loop has nothing to visit.
        parts=_text_segments(base,dict(value,rows=[]),budget)
        rows=value['rows'];start=0;batch=[]
        for index,row in enumerate(rows):
            v=dict(kind='table',rows=batch+[row])
            candidate=dict(base,value=v,row_range=dict(start=start,end=index+1,total_rows=len(rows)))
            if len(_encoded(candidate))>budget:
                if not batch:
                    raise ReaderError('Oversized table row or header')
                old=dict(kind='table',rows=batch)
                parts.append(dict(base,value=old,row_range=dict(start=start,end=index,total_rows=len(rows))))
                start=index;batch=[row]
                candidate=dict(base,value=dict(kind='table',rows=batch),row_range=dict(start=start,end=index+1,total_rows=len(rows)))
                if len(_encoded(candidate))>budget:
                    raise ReaderError('Oversized table row or header')
            else:
                batch.append(row)
        if batch:
            v=dict(kind='table',rows=batch)
            parts.append(dict(base,value=v,row_range=dict(start=start,end=len(rows),total_rows=len(rows))))
        return parts
    raise ReaderError('Oversized atomic reader value')


def _text_segments(base,value,budget):
    """Split text/caption while carrying all value metadata exactly once."""
    text=value if isinstance(value,str) else value['text']
    parts=[];start=0;first=True
    while first or start<len(text):
        empty='' if isinstance(value,str) or not first else dict(value,text='')
        span=dict(start=start,end=start,total_characters=len(text))
        overhead=len(_encoded(dict(base,value=empty,text_range=span)))
        if overhead>budget:
            raise ReaderError('Oversized text metadata')
        # At most six bytes per escaped Unicode codepoint, plus a little range
        # digit growth. A nearly-full metadata record can carry an empty first
        # range; following fragments inherit it and have the normal full budget.
        capacity=max(0,(budget-overhead-32)//6)
        end=min(len(text),start+capacity)
        if end==start and not first and start<len(text):
            raise ReaderError('Unpageable text segment')
        fragment=text[start:end]
        payload=fragment if isinstance(value,str) or not first else dict(value,text=fragment)
        part=dict(base,value=payload,text_range=dict(start=start,end=end,total_characters=len(text)))
        if len(_encoded(part))>budget:
            raise ReaderError('Unpageable text segment')
        parts.append(part);start=end;first=False
    return parts
