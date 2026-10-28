"""Conservative batch review over hash-checked PDF evidence, never live imports.

Section boundaries and unit warnings are review hypotheses. Original cells and
blocks remain separate; a section is not asserted to be a complete recipe.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import re
import unicodedata
from .pdf_assembly import box
from .source_artifacts import is_source_artifact

VERSION = 'pdf-batch-review-1'
Q = r'(?:\d+(?:\.\d+)?(?:\s+\d+/\d+)?[¼½¾⅓⅔⅛⅜⅝⅞]?|[¼½¾⅓⅔⅛⅜⅝⅞])'
PAIR = re.compile(rf'(?P<a>{Q})\s*[- ]?\s*(?P<u>fl\.?\s*oz\.?|fluid ounces?|ounces?|oz\.?|pounds?|lbs?\.?|cups?|tablespoons?|teaspoons?)\s*(?:\(\s*|/\s*)(?P<b>{Q})\s*[- ]?\s*(?P<v>kilograms?|grams?|kg|g|ml|mL|liters?|litres?|l)\b', re.I)

def quantity(value):
    value = unicodedata.normalize('NFKC', re.sub(r'([0-9])([¼½¾⅓⅔⅛⅜⅝⅞])', r'\1 \2', value)).replace('⁄', '/')
    return sum(float(x.split('/')[0])/float(x.split('/')[1]) if '/' in x else float(x) for x in value.split())

def unit_warnings(text):
    """Compare only same-dimension units. Never compare cups with grams."""
    warnings=[]
    for m in PAIR.finditer(text):
        # An additive quantity belongs to the whole expression, not its last term.
        if re.search(r'(?:plus|to|[-–])\s*$', text[max(0,m.start()-12):m.start()]):
            continue
        u=m['u'].lower().rstrip('.'); v=m['v'].lower(); v={'gram':'g','grams':'g','kilogram':'kg','kilograms':'kg'}.get(v,v)
        if v in {'g','kg'} and not u.startswith(('cup','table','tea','fl','fluid')):
            factor=453.59237 if u.startswith(('lb','pound')) else 28.349523125
            actual=quantity(m['b'])*(1000 if v=='kg' else 1)
        elif v in {'ml','l','liter','liters','litre','litres'} and u.startswith(('cup','table','tea','fl','fluid')):
            factor=240 if u.startswith('cup') else 15 if u.startswith('table') else 5 if u.startswith('tea') else 30
            actual=quantity(m['b'])*(1 if v=='ml' else 1000)
        else:continue
        expected=quantity(m['a'])*factor
        if factor == 28.349523125:
            compound=re.search(rf'({Q})\s*[lI1]bs?\.?\s*/?\s*$',text[:m.start()])
            if compound:
                expected += quantity(compound[1])*453.59237
        if expected and abs(actual-expected)/expected>.15:
            warnings.append(dict(kind='same_dimension_unit_disagreement',source_text=m[0],expected_metric=round(expected,2),printed_metric=actual,relative_difference=round(abs(actual-expected)/expected,3),status='needs_source_review'))
    return warnings

def page_view(page):
    lines=[];excluded=[];labels=[]
    for i,c in enumerate(page['cells']):
        l,t,r,b=box(c['rect'],page['size']['height']);w=page['size']['width'];h=page['size']['height']
        text=c['text'];line=dict(pdf_page=page['pdf_page'],cell_index=i,text=text,box=[l/w,t/h,r/w,b/h])
        reason='source_site_artifact' if is_source_artifact(text) else 'empty' if not text.strip() else None
        if t/h>.94 and re.fullmatch(r'(?:[A-Z &-]+\s+)?\d{1,3}',text.strip()):
            labels.append(text.strip().split()[-1]);reason='observed_footer'
        (excluded if reason else lines).append(dict(line,reason=reason) if reason else line)
    if Counter(x['cell_index'] for x in lines+excluded)!=Counter(range(len(page['cells']))):raise ValueError('Cell coverage mismatch')
    return dict(pdf_page=page['pdf_page'],printed_page=labels[0] if len(set(labels))==1 else None,lines=lines,excluded=excluded)

def partition(blocks, profile, pages=None):
    """Retain all blocks once, with provisional header-to-header boundaries."""
    sections=[];excluded=[];current=None
    for b in blocks:
        n=min(b['pdf_pages']);text=b['text'].strip()
        if not profile['body'][0]<=n<=profile['body'][1] or b['kind'] in {'picture','page_footer','page_header'} or is_source_artifact(text):
            excluded.append(dict(block_id=b['id'],reason='outside_body' if not profile['body'][0]<=n<=profile['body'][1] else 'source_site_artifact' if is_source_artifact(text) else b['kind']));continue
        heading=b['kind'] in {'title','section_header'}
        if heading and (re.match(r'^(?:SERVES|MAKES|FOR THE|REGION|PREPARATION|COOKING TIME)\b',text) or text.lower() in {'salt','kosher salt','sea salt','salt and pepper'}):heading=False
        if heading and profile['book_id'] in {'cook-beautiful','mexico'}:heading=text.isupper() and len(text)>5
        if heading and profile['book_id']=='appetite':
            prov=b['provenance'][0];bb=prov['bbox'];h=pages[prov['page_no']]['size']['height']
            top=h-bb['t'] if bb['coord_origin']=='BOTTOMLEFT' else bb['t']
            heading=top/h<.26 and text.casefold() not in {'and more','per person:','you will need:'} and not re.match(r'^(?:Makes|Serves|Per person)',text)
        if heading and current and len(current['block_ids'])==1 and current['pdf_pages']==b['pdf_pages']:
            current['title']+=' '+text
            heading=False
        if heading or current is None:
            current=dict(id='batch_section_'+hashlib.sha256(b['id'].encode()).hexdigest()[:20],title=text if heading else 'Continuation without detected heading',has_detected_heading=heading,block_ids=[],pdf_pages=[],source_text='',status='boundary_candidate',verified_complete_recipe=False)
            sections.append(current)
        current['block_ids'].append(b['id']);current['pdf_pages']=sorted(set(current['pdf_pages'])|set(b['pdf_pages']));current['source_text']+=('\n' if current['source_text'] else '')+text
    if Counter([x for s in sections for x in s['block_ids']]+[x['block_id'] for x in excluded])!=Counter(b['id'] for b in blocks):raise ValueError('Block ownership mismatch')
    return sections,excluded

def geometric_sections(manifest, pages, blocks, profile):
    """Make layout candidates from page bands, not Docling's column order.

    The Italian edition has ingredients at left and preparation at right; the
    Mexican edition anchors each recipe at its REGION metadata. These are
    edition-profiled hypotheses, not proof that each boundary is correct.
    """
    book=profile['book_id']; sections=[];excluded=[];current=None
    by_page={}
    for b in blocks:
        for prov in b['provenance']:
            n=prov['page_no'];bb=prov['bbox'];h=pages[n]['size']['height'];w=pages[n]['size']['width']
            top=(h-bb['t'])/h if bb['coord_origin']=='BOTTOMLEFT' else bb['t']/h
            bottom=(h-bb['b'])/h if bb['coord_origin']=='BOTTOMLEFT' else bb['b']/h
            by_page.setdefault(n,[]).append(dict(text=b['text'],kind=b['kind'],left=bb['l']/w,right=bb['r']/w,top=top,bottom=bottom))
    for n in sorted(pages):
        view=page_view(pages[n]); lines=view['lines'];excluded.extend(view['excluded'])
        if not profile['body'][0]<=n<=profile['body'][1]:
            excluded.extend(dict(l,reason='outside_body') for l in lines);continue
        nodes=by_page.get(n,[]);heads=[]
        if book=='la-cucina':
            for b in nodes:
                t=b['text'].strip()
                if b['kind'] in {'title','section_header'} and t.isupper() and len(t)>3 and (b['left']>.32 or (b['left']>.14 and b['right']>.45)) and not re.search(r'LOCAL TRADITION|FOR \d+ PERSONS',t) and b['top']<.94:
                    heads.append(dict(top=max(0,b['top']-.008),bottom=b['bottom'],title=t,boundary_evidence='right_column_uppercase_title'))
        elif book=='mexico':
            regions=[l for l in lines if l['box'][0]>.32 and re.match(r'REGIONS?\s*:',l['text'])]
            preparations=[l for l in lines if l['box'][0]>.32 and re.match(r'PREPARATION TIME\s*:',l['text'])]
            for l in preparations:
                if not any(0<=l['box'][1]-r['box'][1]<.08 for r in regions):
                    regions.append(l)
            regions.sort(key=lambda l:l['box'][1])
            for j,l in enumerate(regions):
                t=l['box'][1];end=regions[j+1]['box'][1] if j+1<len(regions) else .94
                candidates=[b for b in nodes if b['kind'] in {'title','section_header'} and b['left']<.35 and b['text'].isupper() and t-.02<=b['top']<min(end,t+.17)]
                title=max(candidates,key=lambda b:b['top'])['text'] if candidates else 'Unresolved recipe title'
                heads.append(dict(top=max(0,t-.025),bottom=t,title=title,boundary_evidence='region_metadata_band'))
        else:raise ValueError('No geometric edition policy')
        heads.sort(key=lambda h:h['top'])
        for l in sorted(lines,key=lambda l:(l['box'][1],l['box'][0],l['cell_index'])):
            # Start each band before assigning the line. Carry leading page
            # continuations into the preceding candidate; never discard them.
            while heads and l['box'][1]>=heads[0]['top']:
                h=heads.pop(0)
                current=dict(id='batch_layout_'+hashlib.sha256(f"{manifest['sha256']}:{n}:{h['top']}".encode()).hexdigest()[:18],title=h['title'],start_pdf_page=n,boundary_evidence=h['boundary_evidence'],source_lines=[],pdf_pages=[],status='geometry_candidate_needs_boundary_review',verified_complete_recipe=False)
                sections.append(current)
            if current is None:
                current=dict(id=f'{book}_leading_continuation',title='Leading material without detected boundary',source_lines=[],pdf_pages=[],status='geometry_candidate_needs_boundary_review',verified_complete_recipe=False)
                sections.append(current)
            current['source_lines'].append(deepcopy(l));current['pdf_pages']=sorted(set(current['pdf_pages'])|{n})
    for s in sections:
        s['source_lines'].sort(key=lambda l:(l['pdf_page'],int(l['box'][0]>=.36),l['box'][1],l['box'][0]))
        s['source_text']='\n'.join(l['text'] for l in s['source_lines'])
    expected=Counter((n,i) for n,p in pages.items() for i in range(len(p['cells'])))
    actual=Counter((l['pdf_page'],l['cell_index']) for s in sections for l in s['source_lines'])+Counter((l['pdf_page'],l['cell_index']) for l in excluded)
    if actual!=expected:raise ValueError('Geometry cell ownership mismatch')
    return sections,excluded

def corrected_page_views(manifest, evidence, ledger):
    """Add a source-checked reading layer; original OCR strings stay intact."""
    if ledger['source_sha256']!=manifest['sha256']:raise ValueError('Correction edition mismatch')
    result=deepcopy(evidence);by={(p['pdf_page'],l['cell_index']):l for p in result for l in p['lines']};seen=set()
    for e in ledger['edits']:
        key=(e['pdf_page'],e['cell_index'])
        if key in seen or key not in by or by[key]['text']!=e['before']:raise ValueError('Stale, missing or duplicate correction anchor')
        seen.add(key);by[key]['reading_text']=e['after'];by[key]['source_correction']=deepcopy(e)
    for p in result:p['reading_text']='\n'.join(l.get('reading_text',l['text']) for l in p['lines'])
    return result
