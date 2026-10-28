"""Opt-in edition band review on immutable OCR cells plus reviewed text layers.

This creates review candidates, never qualified recipes or a live import. It
separates sidebars/captions and records old-to-new cell ownership explicitly.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import re

VERSION = 'pdf-cell-band-review-1'


def cell_key(line):
    return line['pdf_page'], line['cell_index']


def method_column(lines):
    starts = [round(l['box'][0], 2) for l in lines
              if l['box'][2] - l['box'][0] > .38
              and .27 < l['box'][0] < .5 and not l['text'].isupper()]
    return Counter(starts).most_common(1)[0][0] if starts else None


def italian_bands(page):
    lines = page['lines']; gutter = method_column(lines)
    markers = [l for l in lines if 'LOCAL TRADITION' in l['text']
               or l['text'].strip(' *+♦') == 'LOCAL']
    sidebars = [dict(top=l['box'][1]-.003, cells=[l['cell_index']],
                     title='Local Tradition', kind='sidebar') for l in markers]
    if gutter is None:
        return [], sidebars, None
    title_lines = []
    for original in lines:
        l = deepcopy(original)
        # OCR occasionally joins the Italian and English titles across the rule.
        if ('|' in l['text'] and l['text'].isupper()
                and l['box'][0] < gutter-.03 and l['box'][2] > gutter+.08):
            l['text'] = l['text'].split('|', 1)[1].strip()
            l['box'][0] = gutter
        text = l['text']
        if (text.isupper() and abs(l['box'][0]-gutter) < .04
                and .02 < l['box'][1] < .935 and len(text) > 2
                and l['box'][2]-l['box'][0] > .065
                and not re.search(r'LOCAL TRADITION|PERSONS|CONTINUED|YIELDS|^FOR (?:[0-9]|$)|TO MAKE', text)):
            title_lines.append(l)
    groups = []
    for l in sorted(title_lines, key=lambda l:l['box'][1]):
        if groups and l['box'][1]-groups[-1][-1]['box'][3] < .025:
            groups[-1].append(l)
        else:
            groups.append([l])
    heads = []
    for group in groups:
        top = group[0]['box'][1]; bottom = max(l['box'][3] for l in group)
        pair = [l for l in lines if l['text'].isupper()
                and gutter-.34 < l['box'][2] < gutter+.005
                and l['box'][0] < gutter-.06
                and top-.018 < l['box'][1] < bottom+.015
                and not l['text'].startswith('CONTINUED')]
        # The Italian heading may wrap above a one-line English heading.
        if pair:
            earlier = sorted((l for l in lines if l['text'].isupper()
                              and gutter-.34 < l['box'][2] < gutter+.005
                              and l['box'][0] < gutter-.06
                              and l['box'][1] < min(p['box'][1] for p in pair)),
                             key=lambda l:l['box'][1], reverse=True)
            for l in earlier:
                if min(p['box'][1] for p in pair)-l['box'][3] >= .025:
                    break
                pair.append(l)
        near_marker = any(top-.12 < l['box'][1] <= top+.01 for l in markers)
        title = ' '.join(l['text'] for l in group)
        if (near_marker and not pair) or title in {'LOCAL', 'TRADITION'}:
            continue
        yields = [l for l in lines if l['box'][0] > gutter+.2
                  and top-.04 < l['box'][1] < bottom+.01
                  and re.match(r'^(?:FOR(?:\s|$)|\d.*PERSONS|TO MAKE|YIELDS)',l['text'])]
        heads.append(dict(top=min([top]+[l['box'][1] for l in pair+yields])-.006,
                          cells=[l['cell_index'] for l in group], title=title,
                          paired_left_title_cells=[l['cell_index'] for l in pair],
                          kind='recipe_candidate'))
    return heads, sidebars, gutter


def rebuild_bands(pages, old_sections, source_sha256, book_id, body):
    """Rebuild reviewed bands; retain every cell and every correction layer.

    Mexico's previously checked REGION/PREPARATION anchors are retained. Its
    sparse intervening pages are captions/chapter furniture, not continuations.
    A dense unanchored page fails closed instead of silently losing recipe text.
    """
    if book_id not in {'la-cucina', 'mexico'}:
        raise ValueError('No reviewed edition policy')
    sections=[]; contexts=[]; excluded=[]; diagnostics=[]; current=None
    by_start=defaultdict(list)
    for s in old_sections:
        by_start[s['start_pdf_page']].append(s)
    gutters={}
    def make(head, n):
        anchor=f"{source_sha256}:{book_id}:{n}:{head['cells'][0]}:{head['kind']}"
        s=dict(id='reviewed_band_'+hashlib.sha256(anchor.encode()).hexdigest()[:20],
               title=head['title'], start_pdf_page=n, kind=head['kind'],
               boundary_evidence=dict(policy=VERSION, **deepcopy(head)),
               source_lines=[], pdf_pages=[], verified_complete_recipe=False,
               status='boundary_review_candidate')
        (sections if head['kind']=='recipe_candidate' else contexts).append(s)
        return s
    for page in pages:
        n=page['pdf_page']; lines=page['lines']+page.get('supplemental_lines', [])
        excluded.extend(deepcopy(page.get('excluded', [])))
        if not body[0] <= n <= body[1]:
            excluded.extend(dict(deepcopy(l), reason='outside_body') for l in lines)
            continue
        if book_id=='la-cucina':
            heads, sidebars, gutter = italian_bands(page)
        else:
            gutter=.37; sidebars=[]; heads=[]
            for old in by_start[n]:
                meta=[l for l in old['source_lines'] if l['pdf_page']==n
                      and l['box'][0]>.32
                      and re.match(r'(?:REGIONS?|PREPARATION TIME)\s*:', l['text'])]
                if not meta:
                    raise ValueError(f'Missing Mexico metadata anchor: {old["id"]}')
                first=min(meta, key=lambda l:l['box'][1])
                heads.append(dict(top=first['box'][1]-.025, cells=[first['cell_index']],
                                  title=old['title'], kind='recipe_candidate'))
            heads.sort(key=lambda h:h['top'])
            if heads:
                heads[0]['top']=0  # These reviewed recipes start on this page.
            elif len(lines)>5:
                raise ValueError(f'Unreviewed dense Mexico continuation page {n}')
        gutters[n]=gutter
        if not heads and not sidebars and (gutter is None or book_id=='mexico'):
            if lines:
                c=make(dict(title='Chapter furniture or photograph caption',
                            top=0, cells=[lines[0]['cell_index']], kind='page_context'),n)
                c['source_lines'].extend(deepcopy(lines))
            current=None
            continue
        events=sorted(heads+sidebars, key=lambda h:h['top']); sidebar=None
        for l in sorted(lines, key=lambda l:(l['box'][1], l['box'][0], l['cell_index'])):
            # Rotated outer-margin running labels are evidence, not recipe text.
            if l['box'][3]-l['box'][1]>.04 and l['box'][2]-l['box'][0]<.04:
                excluded.append(dict(deepcopy(l),reason='vertical_running_label'))
                continue
            while events and l['box'][1]>=events[0]['top']:
                h=events.pop(0); s=make(h,n)
                if h['kind']=='sidebar':sidebar=s
                else:current=s;sidebar=None
            target=sidebar or current
            if target is None:
                target=make(dict(title='Unassigned leading material',top=0,
                                 cells=[l['cell_index']],kind='unassigned_context'),n)
                current=target;diagnostics.append(dict(pdf_page=n,issue='unassigned_leading_material'))
            target['source_lines'].append(deepcopy(l))
        # A sidebar cannot become the next page's recipe continuation.
        # The prior recipe pointer is deliberately preserved when appropriate.
        if events:
            raise ValueError(f'Boundary without source lines on page {n}')
    for s in sections+contexts:
        def order(l):
            g=gutters.get(l['pdf_page']) or .37
            col=int(l['box'][0]>=g-.012) if s['kind']=='recipe_candidate' else 0
            return l['pdf_page'], col, l['box'][1], l['box'][0]
        s['source_lines'].sort(key=order)
        if s['kind']=='sidebar':
            names=[]
            for l in s['source_lines'][1:]:
                text=l.get('reading_text',l['text']).strip(' *+♦')
                if text in {'LOCAL','TRADITION','LOCAL TRADITION'}:
                    continue
                if not text.isupper():
                    break
                names.append(text)
            if names:
                s['title']='Local Tradition: '+' '.join(names)
        s['pdf_pages']=sorted({l['pdf_page'] for l in s['source_lines']})
        for field in ['source_text','reading_text','interpreted_text']:
            def value(l):
                if field=='source_text':return l['text']
                reading=l.get('reading_text',l['text'])
                return l.get('interpreted_text',reading) if field=='interpreted_text' else reading
            s[field]='\n'.join(value(l) for l in s['source_lines'])
        s['printed_pages']=[dict(pdf_page=p['pdf_page'],printed_page=p['printed_page'])
                            for p in pages if p['pdf_page'] in s['pdf_pages']]
    expected=Counter(cell_key(l) for p in pages for l in p['lines']+p.get('excluded',[])+p.get('supplemental_lines',[]))
    actual=Counter(cell_key(l) for s in sections+contexts for l in s['source_lines'])+Counter(cell_key(l) for l in excluded)
    if actual!=expected or any(v!=1 for v in actual.values()):
        raise ValueError('Cell accounting must be exact and unique')
    owner={cell_key(l):s['id'] for s in sections+contexts for l in s['source_lines']}
    crosswalk=[dict(previous_id=s['id'],previous_title=s['title'],
                    targets=dict(Counter(owner.get(cell_key(l),'excluded') for l in s['source_lines'])))
               for s in old_sections]
    return dict(sections=sections,contexts=contexts,excluded=excluded,
                crosswalk=crosswalk,diagnostics=diagnostics,policy=VERSION)
