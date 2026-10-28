"""Bounded source-preserving assembly for three previously passage-only EPUBs.

Assembly is separate from certification of printed quantities. Prose recipes
remain prose, joined ingredient excerpts remain joined, and review holds never
become verified-complete claims. Layout/review artifacts are hash-bound.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import re
import unicodedata

VERSION = 'epub-assembly-1'
CHANG_TITLES = {'RT', 'RT_nobreak', 'RT-NO-YIELD', 'RT-SMALL'}


def apply_quantity_readings(records, ledger):
    """Apply only approved, hash/location-bound readings to a separate copy.

    Original source text is immutable. External comparisons are editorial
    readings, not source-layout normalization or author-approved errata.
    """
    if ledger.get('status') != 'approved_location_bound_readings':
        raise ValueError('Quantity readings are not approved')
    result = deepcopy(records)
    by_id = {r['id']: r for r in result}
    seen = set()
    for entry in ledger['readings']:
        key = (entry['recipe_id'], entry['block_index'])
        if key in seen:
            raise ValueError('Duplicate reading location')
        seen.add(key)
        r = by_id[key[0]]
        if r['source']['sha256'] != entry['source_sha256']:
            raise ValueError('Quantity reading source edition changed')
        b = r['blocks'][key[1]]
        before = b['text']
        if (b['path'] != entry['path'] or b['xpath'] != entry['xpath'] or
                hashlib.sha256(before.encode()).hexdigest() != entry['before_sha256']):
            raise ValueError('Quantity reading location/text changed')
        after = before
        for change in entry['replacements']:
            if not change['before'] or after.count(change['before']) != change['count']:
                raise ValueError('Quantity reading occurrence mismatch')
            after = after.replace(change['before'], change['after'])
        if hashlib.sha256(after.encode()).hexdigest() != entry['after_sha256']:
            raise ValueError('Quantity reading result mismatch')
        b.setdefault('source_text', before)
        b['text'] = after
        r.setdefault('content_edits', []).append(dict(
            entry, kind='external_source_backed_quantity_reading', before=before, after=after))
    for rid in {key[0] for key in seen}:
        r = by_id[rid]
        r['instructions'] = [b['text'] for b in r['blocks'] if b['kind'] in {'instruction', 'prose_recipe'}]
        r['ingredients'] = [b['text'] for b in r['blocks'] if b['kind'] == 'ingredient']
        r['source_prose_excerpt'] = '\n'.join(b['text'] for b in r['blocks'] if b['kind'] == 'prose_recipe')
        r['review']['quantity_readings'] = dict(ledger_id=ledger['id'],
            status='reviewed_locations_resolved', exhaustive_quantity_audit=False)
        r['warnings'] = [w for w in r['warnings'] if w !=
            'Damaged source quantity text is unresolved. This record is held out of the serving candidate.']
        r['warnings'].append('Reviewed quantity readings use matching published recipes; original text and per-location evidence are retained in content_edits. This is not an exhaustive quantity audit or an author erratum.')
    return result


def _apply_legacy_ingredient_boundaries(records, ledger):
    """Reconstruct explicitly reviewed ingredient spans without changing words."""
    if ledger.get('status') != 'reviewed_ingredient_boundaries':
        raise ValueError('Unreviewed ingredient boundaries')
    result = deepcopy(records)
    by_id = {r['id']: r for r in result}
    for entry in ledger['entries']:
        r = by_id[entry['recipe_id']]
        if r['source']['sha256'] != entry['source_sha256']:
            raise ValueError('Ingredient boundary edition changed')
        start, end = entry['block_span']
        original = r['blocks'][start:end]
        signature = hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if signature != entry['blocks_sha256']:
            raise ValueError('Ingredient boundary source blocks changed')
        text = ' '.join(b['text'] for b in original)
        offsets = entry['line_starts'] + [len(text)]
        if offsets[0] != 0 or offsets != sorted(set(offsets)):
            raise ValueError('Invalid ingredient boundary offsets')
        lines = [text[a:z].strip() for a,z in zip(offsets, offsets[1:])]
        if ' '.join(lines) != text:
            raise ValueError('Ingredient boundary edit changed source words')
        r['original_blocks'] = deepcopy(r['blocks'])
        rebuilt = []
        for i,line in enumerate(lines):
            # Each reconstructed line retains its full source span. The text
            # offsets distinguish line boundaries from original paragraph breaks.
            b = dict(kind='ingredient', text=line, tag='reviewed_ingredient_line', classes=[],
                path=original[0]['path'], anchors=[], links=[],
                pages=sorted({p for b in original for p in b.get('pages', [])}),
                source_block_span=[start,end], source_text_offsets=[offsets[i],offsets[i+1]])
            rebuilt.append(b)
        r['blocks'][start:end] = rebuilt
        y = entry['yield_block_index']
        if y >= start or not re.match(r'^(Makes|Serves)\b', r['blocks'][y]['text']):
            raise ValueError('Invalid reviewed yield location')
        r['blocks'][y]['kind'] = 'yield'
        r['yield'] = r['blocks'][y]['text']
        r['ingredients'] = lines
        r['instructions'] = [b['text'] for b in r['blocks'] if b['kind'] in {'instruction','prose_recipe'}]
        r['source_prose_excerpt'] = '\n'.join(b['text'] for b in r['blocks'] if b['kind']=='prose_recipe')
        r['record_kind'] = 'recipe'
        r.setdefault('content_edits', []).append(dict(entry,
            kind='reviewed_ingredient_boundary_reconstruction', before=text, after='\n'.join(lines)))
        r['review']['ingredient_representation'] = 'reviewed ingredient lines; original damaged paragraphs retained in original_blocks'
        r['warnings'] = ['Source ingredient paragraphs were joined or broken mid-ingredient. Reviewed boundaries restore separate lines without changing source words or quantities; original_blocks and source_text retain the original.']
    return result


def apply_ingredient_boundaries(records, ledger):
    """Apply hash-bound, reviewed offsets; never infer ingredient cuts at import."""
    if ledger.get('status') != 'reviewed_ingredient_boundaries':
        raise ValueError('Unreviewed ingredient boundaries')
    ids = [e['recipe_id'] for e in ledger['entries']]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate ingredient boundary records')
    legacy = [e for e in ledger['entries'] if 'spans' not in e]
    result = _apply_legacy_ingredient_boundaries(records, dict(ledger, entries=legacy))
    by_id = {r['id']: r for r in result}
    digest = lambda v: hashlib.sha256(json.dumps(v, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    for entry in ledger['entries']:
        if 'spans' not in entry:
            continue
        r = by_id[entry['recipe_id']]
        if r['source']['sha256'] != entry['source_sha256']:
            raise ValueError('Ingredient boundary edition changed')
        if 'original_blocks' in r:
            raise ValueError('Ingredient boundaries already applied')
        original = deepcopy(r['blocks'])
        replacements = {}
        occupied = set()
        edits = []
        for span in entry['spans']:
            start, end = span['block_span']
            if not 0 <= start < end <= len(original) or occupied.intersection(range(start, end)):
                raise ValueError('Invalid or overlapping ingredient spans')
            occupied.update(range(start, end))
            source = original[start:end]
            if digest(source) != span['blocks_sha256']:
                raise ValueError('Ingredient boundary source blocks changed')
            text = ' '.join(b['text'] for b in source)
            parts = span['parts']
            cursor = 0
            rebuilt = []
            allowed = set(span.get('joined_word_boundaries', []))
            used_joined = set()
            for part in parts:
                a, z = part['offsets']
                kind = part['kind']
                if a != cursor or not a < z <= len(text) or kind not in {'ingredient', 'ingredient_group', 'prose_recipe'}:
                    raise ValueError('Invalid ingredient part offsets or kind')
                if a and not (text[a-1].isspace() or text[a].isspace()):
                    if a not in allowed:
                        raise ValueError('Unreviewed word split')
                    used_joined.add(a)
                line = text[a:z].strip()
                if not line:
                    raise ValueError('Empty ingredient part')
                # Retain exact contributing block locations, including links and
                # anchors. A line may cross paragraphs or share one with a method.
                locations = []
                pos = 0
                for index, b in enumerate(source, start):
                    stop = pos + len(b['text'])
                    if a < stop and z > pos:
                        locations.append(dict(block_index=index, text_offsets=[max(a-pos, 0), min(z-pos, len(b['text']))],
                            path=b['path'], xpath=b.get('xpath'), anchors=deepcopy(b.get('anchors', [])),
                            links=deepcopy(b.get('links', [])), pages=deepcopy(b.get('pages', []))))
                    pos = stop + 1
                rebuilt.append(dict(kind=kind, text=line, tag='reviewed_ingredient_boundary', classes=[],
                    path=locations[0]['path'], anchors=list(dict.fromkeys(x for loc in locations for x in loc['anchors'])),
                    links=[x for loc in locations for x in loc['links']],
                    pages=sorted({x for loc in locations for x in loc['pages']}),
                    source_block_span=[start, end], source_text_offsets=[a, z],
                    source_segments=locations, source_block_index_space='original_blocks'))
                cursor = z
            if cursor != len(text) or allowed != used_joined:
                raise ValueError('Incomplete ingredient span or unused word-boundary approval')
            if ''.join(text.split()) != ''.join(''.join(b['text'] for b in rebuilt).split()):
                raise ValueError('Ingredient boundary edit changed source characters')
            replacements[start] = (end, rebuilt)
            edits.append(dict(span, kind='reviewed_ingredient_boundary_reconstruction',
                source_block_index_space='original_blocks', before=text,
                after='\n'.join(b['text'] for b in rebuilt), evidence=entry['evidence']))
        roles = {}
        for role in entry.get('block_roles', []):
            i = role['block_index']
            if i in occupied or i in roles or not 0 <= i < len(original):
                raise ValueError('Invalid ingredient section label location')
            if digest(original[i]) != role['block_sha256'] or role['kind'] != 'ingredient_group':
                raise ValueError('Ingredient section label changed')
            roles[i] = role['kind']
        rebuilt = []
        i = 0
        while i < len(original):
            if i in replacements:
                end, parts = replacements[i]
                rebuilt.extend(parts)
                i = end
            else:
                b = deepcopy(original[i])
                if i in roles:
                    b['kind'] = roles[i]
                b['source_block_index'] = i
                b['source_block_index_space'] = 'original_blocks'
                rebuilt.append(b)
                i += 1
        r['original_blocks'] = original
        r['blocks'] = rebuilt
        # Earlier quantity decisions address the pre-structural block numbering.
        for edit in r.get('content_edits', []) + r.get('normalizations', []):
            if 'block_index' in edit:
                edit['block_index_space'] = 'original_blocks'
        r.setdefault('content_edits', []).extend(edits)
        r['ingredients'] = [b['text'] for b in rebuilt if b['kind'] == 'ingredient']
        r['instructions'] = [b['text'] for b in rebuilt if b['kind'] in {'instruction', 'prose_recipe'}]
        r['source_prose_excerpt'] = '\n'.join(b['text'] for b in rebuilt if b['kind'] == 'prose_recipe')
        r['record_kind'] = 'recipe'
        r['review']['ingredient_representation'] = 'reviewed ingredient lines and section labels; original paragraphs retained in original_blocks'
        r['review']['ingredient_structure_review'] = entry['evidence']
        r['original_warnings'] = deepcopy(r.get('warnings', []))
        r['warnings'] = [w for w in r.get('warnings', []) if w != 'Ingredients, method, variations and commentary remain in source paragraph order. This edition joins some ingredient lines and has text defects; no quantities or missing separators have been inferred. Read the full source excerpt.']
        r.setdefault('warnings', []).append('Ingredient boundaries reviewed; source characters and quantities preserved. This structural review does not verify quantities or recipe completeness. Original paragraphs and correction evidence remain available.')
        r['warnings'].extend(entry.get('warnings', []))
    return result


def record(book, source, title, blocks, *, aliases=(), warnings=(), representation='source prose'):
    blocks = deepcopy(blocks)
    title_block = next(b for b in blocks if b['kind'] == 'title')
    locator = title_block.get('xpath') or str(title_block.get('run_indices'))
    rid = hashlib.sha256(f"{book['id']}:{title_block['path']}:{locator}".encode()).hexdigest()[:16]
    original = '\n'.join(b.get('source_text', b['text']) for b in blocks)
    changes = [dict(block_index=i, before=b['source_text'], after=b['text'],
                    kind='source_layout_normalization') for i,b in enumerate(blocks)
               if b.get('source_text',b['text']) != b['text']]
    return dict(id=rid, book_id=book['id'], book=book['title'], author=book['author'],
        title=title, aliases=list(aliases), record_kind='recipe' if any(b['kind']=='ingredient' for b in blocks) else 'prose_recipe',
        ingredients=[b['text'] for b in blocks if b['kind']=='ingredient'],
        instructions=[b['text'] for b in blocks if b['kind'] in {'instruction','prose_recipe'}],
        headnote='\n'.join(b['text'] for b in blocks if b['kind']=='headnote'),
        **{'yield':next((b['text'] for b in blocks if b['kind']=='yield'),None)},
        blocks=blocks, source_text=original, source_prose_excerpt='\n'.join(b['text']for b in blocks if b['kind']=='prose_recipe'), warnings=list(warnings), normalizations=changes,
        source=dict(source,format='epub',extractor=VERSION,member=title_block['path'],
            anchor=next(iter(title_block.get('anchors',[])),''),
            printed_page=next(iter(title_block.get('pages',[])),None)),
        references=[],supporting_notes=[],extraction_status='assembled_with_retained_limitations',
        review=dict(status='assembly_candidate',verified_complete_recipe=False,personally_tested=None,
                    ingredient_representation=representation))


def assemble_chang(book, source, blocks):
    """Explicit publisher recipe styles; reverse formula headings are intentional."""
    result=[]; current=[]; formula=[]
    def finish():
        if not current:return
        variation=False
        for i,b in enumerate(current):
            cs=set(b['classes'])
            if i==0:b['kind']='title'
            elif b.get('kind')=='image':continue
            elif cs & {'RY','RY-SMALL','RVY'}:b['kind']='yield' if not variation else 'note'
            elif cs & {'RVT','RVT-WITH-YIELD-BELOW'}:b['kind']='variation_title';variation=True
            elif variation:b['kind']='note'
            elif cs & {'RECIPE-FOOTNOTE','PK-NOTE'}:b['kind']='note'
            elif 'RIL-SMALL'in cs:b['kind']='ingredient'
            else:b['kind']='prose_recipe'
        result.append(record(book,source,current[0]['text'],current,
            warnings=['Source uses flexible prose. Quantities and method narration are retained as written; no ingredient list has been inferred. Variations remain separately labelled in the ordered blocks.']))
    for b in deepcopy(blocks):
        cs=set(b['classes'])
        if 'RT_sum'in cs:
            if not formula:raise ValueError('Formula heading without preceding body')
            for f in formula:f['kind']='headnote' if any(c.startswith('EQUATION-1')for c in f['classes']) else 'prose_recipe'
            b['kind']='title';formula.append(b)
            result.append(record(book,source,b['text'],formula,warnings=['Source formula has its title after the body. Original order retained; no inferred ingredient list.']))
            formula=[];continue
        if any(c in {'EQUATION-1','EQUATION-1no'}for c in cs):
            finish();current=[];formula=[b];continue
        if formula:formula.append(b);continue
        title=bool(cs & CHANG_TITLES) or ('SBH1'in cs and b['text']=='Chawanmushi')
        barrier=any(re.match(r'^(?:PT|CT|H[1-4]|SBH1)',c)for c in cs) or b['tag']in{'h1','h2','h3'}
        if current and b['path']!=current[0]['path']:finish();current=[]
        if title or barrier:
            finish();current=[b]if title else []
        elif current:current.append(b)
        elif result and result[-1]['title']=='Pimento cheese' and b['path'].endswith('c07.xhtml'):
            b['kind']='variation_title' if cs & {'RVT','RVT-WITH-YIELD-BELOW'} else 'note'
            result[-1]['blocks'].append(b);result[-1]['source_text']+='\n'+b['text']
    finish()
    if formula:raise ValueError('Unclosed formula')
    return result


# These are not recipe starts: explicit ingredient groups, folklore, prose,
# and continued lines of titles. Decisions refer to the hash-bound source.
RODEN_NON_START = {79,87,122,123,125,126,130,144,148,181,193,205,250,251,258,264,271,272,
    298,323,332,333,334,335,336,364,365,370,377,381,382,392,393,395,397,398,399,406,407,
    411,441,450,451,452,454,455,456,457,466,467,484,485,528}
RODEN_CONTEXT_BOUNDARIES = {87,193,250,251,258,264,271,272,364,365,370,392,393,398,399,
    406,407,441,450,451,452,466,467,484,485}
# Missing publisher title formatting; reviewed against surrounding source text.
RODEN_EXTRA_STARTS = {588,646,1084,3382,4456,4484,4509,4533,4666,5433,6441,6961,7215,
                      7426,7498,7525,7550,7569,7581,7588,7616,7629,7647,7652,7663,7670,7680,7701,7752,7817}
RODEN_JOINED_TITLES = {322:2,339:3,380:3,410:2}


def assemble_roden(book, source, blocks, headings):
    starts={h['index']:dict(h) for h in headings if h['n']not in RODEN_NON_START}
    # The first line of this two-line title is plain text, not bold.
    special=next(h for h in headings if h['n']==414)
    starts.pop(special['index']); starts[special['index']-1]=dict(special,index=special['index']-1,title='')
    for i in RODEN_EXTRA_STARTS:starts[i]=dict(index=i,n=None,title=blocks[i]['text'])
    barriers={h['index']for h in headings if h['n']in RODEN_CONTEXT_BOUNDARIES}
    barriers|={i for i,b in enumerate(blocks)if b['tag']in{'h1','h2','h3'}}
    result=[]
    for i,h in sorted(starts.items()):
        end=next((j for j in range(i+1,len(blocks))if j in starts or j in barriers or blocks[j]['path']!=blocks[i]['path']),len(blocks))
        section=deepcopy(blocks[i:end]); count=RODEN_JOINED_TITLES.get(h['n'],1)
        if i==646:count=3
        if i==special['index']-1:count=2
        title=' '.join(b['text']for b in section[:count]);aliases=[]
        for k,b in enumerate(section):
            if k<count:b['kind']='title'
            elif k==count and (b['italic']or(i==6463 and b['text']=='Arab Bread—Pita')) and not re.match(r'(?:Serves|Makes)\b',b['text']):
                b['kind']='subtitle';aliases.append(b['text'])
            elif b.get('kind')=='image':continue
            else:b['kind']='prose_recipe'
        # Deliberately retain original paragraphs instead of guessing boundaries
        # inside damaged joined lists or silently fixing OCR-like quantities.
        warnings=['Ingredients, method, variations and commentary remain in source paragraph order. This edition joins some ingredient lines and has text defects; no quantities or missing separators have been inferred. Read the full source excerpt.']
        r=record(book,source,title,section,aliases=aliases,warnings=warnings,
            representation='unparsed source recipe paragraphs; joined ingredient lists retained')
        result.append(r)
    return result


def rendered_runs(page, glyph_map):
    """Preserve glyph/run provenance while repairing explicit visual word gaps."""
    out=[]
    for run in page['runs']:
        r=deepcopy(run);font=next(c for c in r['classes']if re.fullmatch(r'f\d+',c));parts=r['parts'];text=''
        for i,p in enumerate(parts):
            if i and text and p['text'] and not text[-1].isspace() and not p['text'][0].isspace():
                if p['x']-parts[i-1]['right']>r['font_size']*.17:text+=' '
            text+=p['text']
        text=''.join(glyph_map.get((font,c),c)for c in text)
        text=text.translate(str.maketrans({'ﬀ':'ff','ﬁ':'fi','ﬂ':'fl','ﬃ':'ffi','ﬄ':'ffl','\u00ad':''}))
        if any(0xe000<=ord(c)<=0xf8ff for c in text):raise ValueError('Unreviewed private glyph')
        r['original_text']=run['text'];r['text']=text;out.append(r)
    return out


def layout_lines(runs, *, tolerance=1.2):
    """Group baselines, then use measured gaps, not DOM run order."""
    runs=deepcopy(runs)
    for r in runs:
        if re.fullmatch(r'[⁰¹²³⁴⁵⁶⁷⁸⁹]+',r['text']) or (r['font_size']<13 and re.fullmatch(r'[0-9]+',r['text'])):
            peers=[q for q in runs if not re.fullmatch(r'[⁰¹²³⁴⁵⁶⁷⁸⁹]+',q['text']) and 0<q['baseline']-r['baseline']<10 and abs(q['right']-r['x'])<3]
            if peers:
                r['source_baseline']=r['baseline'];r['baseline']=min(peers,key=lambda q:abs(q['right']-r['x']))['baseline']
                r['text']=r['text'].translate(str.maketrans('0123456789','⁰¹²³⁴⁵⁶⁷⁸⁹'))
    groups=[]
    # Original baseline from CSS position is independent of glyph/font height.
    for r in sorted(runs,key=lambda r:(r['baseline'],r['x'])):
        group=next((g for g in reversed(groups)if abs(g[0]['baseline']-r['baseline'])<=tolerance),None)
        if group is None:groups.append([r])
        else:group.append(r)
    out=[]
    for group in groups:
        group.sort(key=lambda r:r['x']);text=''
        for i,r in enumerate(group):
            if i and text and r['text'] and not text[-1].isspace() and not r['text'][0].isspace():
                if r['x']-group[i-1]['right']>min(r['font_size'],group[i-1]['font_size'])*.17:text+=' '
            text+=r['text']
        out.append(dict(text=' '.join(text.split()),original_text=''.join(r['original_text']for r in group),
            x=min(r['x']for r in group),baseline=group[0]['baseline'],
            run_indices=[r['index']for r in group],classes=sorted({c for r in group for c in r['classes']}),runs=group))
    return out

NAGI_CONTINUATIONS = {44:[45],50:[51],106:[107],116:[117],150:[151],164:[165],173:[174],
    186:[187],210:[211],212:[213],214:[215],240:[241],244:[245],250:[251],
    252:[253,254,255,256],258:[259],260:[261,262],274:[275],276:[277,278],
    281:[282],285:[286],288:[289],290:[291],294:[295],304:[305],308:[309],311:[312],318:[319],320:[321]}
NAGI_NOTES_PAGES = {174,256,262,278,282,286,312}


def nagi_content(page):
    return [r for r in page['runs']if 70<r['x']<1090 and r['baseline']<1405
            and not any(c.startswith('t')and c[1:].isdigit()for c in r['classes'])]


def layout_block(lines, kind, member):
    n=int(re.search(r'page(\d+)',member)[1]);runs=[r for line in lines for r in line['runs']]
    text=' '.join(line['text']for line in lines)
    return dict(kind=kind,text=text,source_text='\n'.join(line['original_text']for line in lines),
        tag='rendered_epub_excerpt',classes=[],path=member,pages=[str(n)],anchors=[],links=[],
        run_indices=[r['index']for r in runs],source_runs=deepcopy(runs),
        xpath='runs/'+','.join(str(r['index'])for r in runs))


def paragraph_blocks(lines,kind,member,gap=26):
    groups=[]
    for line in lines:
        if not groups or line['baseline']-groups[-1][-1]['baseline']>gap:groups.append([line])
        else:groups[-1].append(line)
    return [layout_block(g,kind,member)for g in groups]


def nagi_body(runs,member,*,column=391,body_start=None,notes_only=False):
    if notes_only:return paragraph_blocks(layout_lines(runs),'note',member)
    if body_start is None:
        body_start=min((r['baseline']for r in runs if abs(r['x']-column)<3 and 'fs7'in r['classes'] and not any(q['x']<column-10<q['right'] and abs(q['baseline']-r['baseline'])<1.2 for q in runs)),default=float('inf'))
    full_note_at=min((l['baseline'] for l in layout_lines([r for r in runs if r['x']<column]) if re.fullmatch(r'NOTES?',l['text'])),default=float('inf'))
    if member.endswith('335.xhtml'):full_note_at=min(full_note_at,920)
    if member.endswith('347.xhtml'):full_note_at=min(full_note_at,1100)
    full_notes=[r for r in runs if r['baseline']>=full_note_at-2]
    runs=[r for r in runs if r['baseline']<full_note_at-2]
    above=[r for r in runs if r['baseline']<body_start-2]
    blocks=[]
    wrap_gap=18.5 if member.endswith('017.xhtml') else 26
    for line in layout_lines(above):
        kind='yield'if re.search(r'\b(?:SERVES|MAKES|PREP|COOK):?',line['text'])and ('fs11'in line['classes']or'fs17'in line['classes'])else'headnote'
        blocks.append(layout_block([line],kind,member))
    left=layout_lines([r for r in runs if r['baseline']>=body_start-2 and r['x']<column-10])
    # Keep explicit ingredient groups and each wrapped source item intact.
    group=[]
    for line in left:
        header='fs14'in line['classes'] and line['text'].isupper()
        if header or (group and line['baseline']-group[-1]['baseline']>wrap_gap):
            if group:blocks.append(layout_block(group,'ingredient',member));group=[]
        if header:blocks.append(layout_block([line],'ingredient_group',member))
        else:group.append(line)
    if group:blocks.append(layout_block(group,'ingredient',member))
    right=layout_lines([r for r in runs if r['baseline']>=body_start-2 and r['x']>=column-10])
    note_at=next((i for i,l in enumerate(right)if re.fullmatch(r'NOTES?|LEFTOVERS.*',l['text'])),len(right))
    blocks+=paragraph_blocks(right[:note_at],'instruction',member,gap=wrap_gap)
    blocks+=paragraph_blocks(right[note_at:],'note',member,gap=wrap_gap)
    blocks+=paragraph_blocks(layout_lines(full_notes),'note',member)
    return blocks


def assemble_nagi(book,source,pages):
    by_number={int(re.search(r'page(\d+)',p['member'])[1]):p for p in pages};result=[]
    for n,page in sorted(by_number.items()):
        if n in {96,97,225,351}:continue # general technique/table, retained as knowledge
        runs=nagi_content(page);titles=layout_lines([r for r in runs if 'fs19'in r['classes']])
        if n==17:
            runs=[r for r in runs if r['baseline']>370 and r['baseline']<990 and r['x']<805]
            titles=layout_lines([r for r in runs if 'fs2'in r['classes']])
        if not titles:continue
        groups=[]
        for line in titles:
            if not groups or line['baseline']-groups[-1][-1]['baseline']>60:groups.append([line])
            else:groups[-1].append(line)
        for k,g in enumerate(groups):
            low=min(l['baseline']for l in g)-2;high=min(l['baseline']for l in groups[k+1])-2 if k+1<len(groups)else float('inf')
            title_ids={i for l in g for i in l['run_indices']};selected=[r for r in runs if low<=r['baseline']<high and r['index']not in title_ids]
            blocks=[layout_block(g,'title',page['member'])]
            blocks+=nagi_body(selected,page['member'],column=305 if n==17 else 391,body_start=587 if n==17 else float('inf') if n in set(NAGI_CONTINUATIONS)-{173,252,260,276,281,285,308,311} else None)
            for follow in NAGI_CONTINUATIONS.get(n,[]):
                p=by_number[follow];rr=nagi_content(p)
                if follow==211:
                    # A real source table has separate per-burrito and batch
                    # columns. Retain cells with headers; never flatten doses.
                    cols=[layout_lines([r for r in rr if a<=r['x']<z and r['baseline']<715])for a,z in [(70,510),(510,795),(795,1090)]]
                    left_groups=[]
                    for line in cols[0]:
                        if not left_groups or line['baseline']-left_groups[-1][-1]['baseline']>26:left_groups.append([line])
                        else:left_groups[-1].append(line)
                    table=layout_block([line for col in cols for line in col],'table',p['member']);table['rows']=[]
                    for gi,group in enumerate(left_groups):
                        low=group[0]['baseline']-3;high=left_groups[gi+1][0]['baseline']-3 if gi+1<len(left_groups)else 715
                        table['rows'].append([' '.join(l['text']for l in group)]+[' '.join(l['text']for l in col if low<=l['baseline']<high)for col in cols[1:]])
                    table['text']='\n'.join(' | '.join(row)for row in table['rows']);blocks.append(table)
                    blocks+=nagi_body([r for r in rr if r['baseline']>=740],p['member'],column=87,body_start=740)
                elif follow==254:blocks+=paragraph_blocks(layout_lines(rr),'instruction',p['member'])
                elif follow==255:
                    for a,z in [(70,575),(575,1090)]:blocks+=paragraph_blocks(layout_lines([r for r in rr if a<=r['x']<z]),'note',p['member'])
                elif follow==309:
                    # Ten independent flavour callouts positioned around a photo.
                    for a,z,top,bottom in [(610,850,90,220),(340,610,120,250),(870,1090,170,350),
                        (110,340,300,500),(870,1090,470,605),(960,1090,640,730),(70,340,770,920),
                        (70,340,975,1150),(240,600,1170,1340),(745,1090,1060,1300)]:
                        section=[r for r in rr if a<=r['x']<z and top<=r['baseline']<bottom]
                        blocks+=paragraph_blocks(layout_lines(section),'note',p['member'])
                else:blocks+=nagi_body(rr,p['member'],body_start=0,notes_only=follow in NAGI_NOTES_PAGES)
            title=' '.join(l['text']for l in g)
            aliases=[{'PAN-FRY IT':'Pan-fried chicken breast','POACH IT':'Poached chicken breast','BAKE IT':'Baked chicken breast'}[title]]if title in {'PAN-FRY IT','POACH IT','BAKE IT'}else[]
            r=record(book,source,title,blocks,aliases=aliases,
                representation='source ingredient excerpts joined only across visual wrapped lines',
                warnings=['Source column order and embedded-font digits reconstructed. Original text runs and layout positions are retained. Asterisks refer to the book glossary.'])
            if n==210:r['warnings'].append('Burrito quantities remain in three labelled table columns: ingredient, for 12 burritos, and per burrito. Read all three columns together; no dose column selected automatically.')
            result.append(r)
    # Eight dressings use one shared source instruction on the facing page.
    page=by_number[333];runs=nagi_content(page)
    for a,z in [(70,330),(330,580),(580,840),(840,1090)]:
        for top,bottom in [(260,550),(770,1090)]:
            rr=[r for r in runs if a<=r['x']<z and top<=r['baseline']<bottom]
            title_runs=[r for r in rr if'fs23'in r['classes']];lines=layout_lines(title_runs)
            if not lines:raise ValueError('Missing dressing title')
            blocks=[layout_block(lines,'title',page['member'])]
            blocks+=paragraph_blocks(layout_lines([r for r in rr if'fs23'not in r['classes']]),'ingredient',page['member'])
            shared=layout_lines(nagi_content(by_number[332]));blocks+=paragraph_blocks(shared,'instruction',by_number[332]['member'])
            blocks+=paragraph_blocks(layout_lines([r for r in runs if r['baseline']>1200]),'note',page['member'])
            result.append(record(book,source,' '.join(l['text']for l in lines),blocks,
                representation='source ingredient excerpts with explicit shared facing-page method',
                warnings=['Shared dressing method and serving guidance come from the facing source page, retained with separate provenance.']))
    return result
