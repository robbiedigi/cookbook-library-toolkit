"""Rebuildable, edition-bound book-index relationships; never recipe promotion.

Raw index artifacts are inputs, not rewritten output. Resolution and ingredient
validation are independent. Only a corroborated link to a current recipe can be
used by the optional broad discovery scope.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from .search import canonical_ingredient, ingredient_matches, INGREDIENTS

VERSION = 'index-relationships-1'
STOP = set('a an the and with in of for on to from by at de la el con'.split())
# Deliberately bounded ingredient identities. Unknown index topics remain topics,
# even if a coincidental word occurs in a method. This is not a dietary ontology.
FOODS = set(INGREDIENTS) | set('''banana leaf|corn husk|avocado leaf|banana|prosciutto|bacon|pepperoni|anchovy|black vinegar|vinegar|soy sauce|fish sauce|sesame|sesame oil|olive oil|oil|chile|chili|chilli|shallot|scallion|onion|coconut|coconut milk|milk|buttermilk|flour|sugar|honey|maple syrup|agave syrup|salt|pepper|black pepper|white pepper|cumin|coriander|fennel seed|mustard|mustard seed|bay leaf|thyme|rosemary|sage|oregano|epazote|hoja santa|purslane|nopales|cactus paddle|ayocote bean|cashew|pistachio|hazelnut|pine nut|pecan|apricot|plum|grape|raisin|date|fig|pineapple|mango|melon|watermelon|rhubarb|strawberry|raspberry|blackberry|blueberry|cranberry|tamarind|lemongrass|galangal|turmeric|cardamom|cinnamon|clove|vanilla|saffron|chard|endive|chicory|lettuce|pea|snow pea|snap pea|green bean|fava bean|broad bean|cauliflower|broccolini|brussels sprout|artichoke|celeriac|turnip|rutabaga|parsnip|sweet potato|watercress|sorrel|dill|tarragon|chervil|chive|masa|tomatillo|mussel|clam|oyster|scallop|crab|lobster|squid|octopus|cod|halibut|trout|tuna|sardine|mackerel|sea bass|snapper|fish|pancetta|ham|sausage|chorizo|veal|rabbit|turkey|quail|yogurt|ricotta|feta|mozzarella|goat cheese|cream cheese|crème fraîche|chocolate|cocoa|coffee|tea|bourbon|wine|beer|rum|brandy|gelatin|chia seed|amaranth|quinoa|barley|oat|bulgur|couscous|noodle|bread|tortilla'''.split('|'))
IRREGULAR = {'leaves':'leaf','anchovies':'anchovy','berries':'berry','cherries':'cherry',
             'tomatoes':'tomato','potatoes':'potato','chilies':'chili','chillies':'chilli',
             'knives':'knife','loaves':'loaf','peaches':'peach','radishes':'radish'}


def norm(text):
    text = ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))
    text = text.replace('(s)', 's').replace('(ies)', 'ies')
    return ' '.join(re.findall(r'[a-z0-9]+', text))


def singular(word):
    if word in IRREGULAR: return IRREGULAR[word]
    return word[:-1] if word.endswith('s') and not word.endswith(('ss','us')) else word


def tokens(text):
    return frozenset(singular(w) for w in norm(text).split() if w not in STOP)


def identity(label):
    label = re.sub(r'\([^)]*\)', '', label.replace('(s)','s')).strip(' ,:;-')
    value = canonical_ingredient(' '.join(singular(w) for w in norm(label).split()))
    return value if value in FOODS else None


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def recipe_fingerprint(recipes):
    return digest({r['id']:r for r in recipes})


def locators(entry):
    result = []
    for ref in entry.get('page_references', entry.get('printed_locators', [])):
        if ref.get('target_book_id') and ref['target_book_id'] != entry.get('source', {}).get('book_id'):
            # Retain cumulative-index evidence without matching another
            # volume's page number against this book's recipes.
            continue
        pages = ref.get('printed_pages')
        if pages is None:
            from .pdf_index import printed_references
            try: pages = [p for g in printed_references(ref['source_text']) for p in g['printed_pages']]
            except (ValueError,KeyError): pages = []
        result += [dict(printed_page=str(p),pdf_page=None,kind=ref.get('locator_kind','unknown')) for p in pages]
    for link in entry.get('links',[]):
        if link.get('kind') == 'index_reference': continue
        page=link.get('printed_page')
        if not page and (m:=re.fullmatch(r'(?:page_?|p)(\d+)',link.get('anchor',''))): page=m[1]
        result.append(dict(printed_page=page,pdf_page=None,member=link.get('member'),anchor=link.get('anchor'),kind=link.get('locator_kind','unknown')))
    return result


def headings(recipe):
    yield (recipe['title'],None)
    for alias in recipe.get('aliases',[]):
        yield (alias,None)
    for i,b in enumerate(recipe.get('blocks',[])):
        if b.get('kind') in ('heading','ingredient_group','component_title','section_header') and len(tokens(b['text']))>=1:
            if tokens(b['text']) != tokens(recipe['title']):
                yield (b['text'],f"{recipe['id']}:block:{i}")


def pages(recipe):
    printed=recipe.get('printed_pages',[])
    if isinstance(printed,dict): printed=printed.values()
    return {str(p) for p in printed if p} | {str(p) for b in recipe.get('blocks',[]) for p in b.get('pages',[]) if p} | ({str(recipe['source']['printed_page'])} if recipe.get('source',{}).get('printed_page') else set())


def resolve_entry(entry, recipes):
    """No page-only acceptance, including single-recipe pages.

    Reordered/inherited index titles require complete heading tokens and a
    unique destination; genuine non-page heading anchors can preserve aliases.
    Locator disagreement is recorded, never rewritten.
    """
    label=tokens(entry['label']); hierarchy=tokens(' '.join(entry.get('hierarchy',[entry['label']])))
    locations=locators(entry); printed={x['printed_page'] for x in locations if x['kind']!='photograph' and x['printed_page']}
    members={x['member'] for x in locations if x.get('member') and x['kind']!='photograph'}
    anchors={rid for link in entry.get('links',[]) if link.get('destination_kind')=='source_anchor' and link.get('locator_kind')!='photograph' for rid in link.get('source_anchor_recipe_candidate_ids',[])}
    matches=[]; neighbors=[]
    for r in recipes:
        overlap=bool(printed & (r["_pages"] if "_pages" in r else pages(r))); member=r.get('source',{}).get('member') in members
        if overlap: neighbors.append(r['id'])
        for heading,component,h in (r["_headings"] if "_headings" in r else [(h,c,tokens(h)) for h,c in headings(r)]):
            if not h: continue
            exact=h==label or h==hierarchy
            inherited=len(h)>=2 and h<=hierarchy and len(label & h)>=2 and len(label-h)<=1
            anchored=r['id'] in anchors and component is None
            if not (exact or inherited or anchored): continue
            # Partial inherited labels need source location; exact complete
            # titles can recover omitted locators without speculative proximity.
            if inherited and not exact and not (overlap or member): continue
            score=(6 if exact else 4 if inherited else 3)+(2 if overlap else 0)+(1 if member else 0)
            matches.append((score,r,heading,component,'exact_heading' if exact else 'inherited_heading' if inherited else 'source_heading_anchor',overlap))
    if not matches:
        return dict(status='unresolved',recipe_id=None,component_id=None,candidate_ids=sorted(neighbors),evidence=[],correction=None)
    best=max(m[0] for m in matches);top=[m for m in matches if m[0]==best]
    destinations={(m[1]['id'],m[3]) for m in top}
    if len(destinations)!=1:
        return dict(status='ambiguous',recipe_id=None,component_id=None,candidate_ids=sorted({m[1]['id'] for m in top}),evidence=[],correction=None)
    _,r,heading,component,basis,overlap=top[0]
    candidate=r.get('_candidate',False)
    correction=None
    if printed and not overlap:
        correction=dict(original_printed_pages=sorted(printed),destination_printed_pages=sorted(pages(r)),
            reason='Unique complete heading/anchor evidence; original locator differs or destination page labels are unavailable.',
            kind='locator_disagreement' if pages(r) else 'unmapped_locator')
    return dict(status='candidate_resolved' if candidate else 'resolved',recipe_id=None if candidate else r['id'],
        candidate_id=r['id'] if candidate else None,component_id=component,candidate_ids=[r['id']],
        evidence=[dict(basis=basis,matched_heading=heading,source=r.get('source',{}),
                       printed_pages=sorted(pages(r)),pdf_pages=r.get('pdf_pages',[]),locator_agrees=overlap)],correction=correction)


def validate_ingredient(term, recipe, component_id=None):
    if not term: return dict(status='not_an_ingredient',roles=[],evidence=[])
    evidence=[];roles=set()
    # Only ingredient and method blocks corroborate ordinary use. Narrative
    # mentions remain review candidates because negation/variants may conflict.
    blocks=recipe.get('blocks',[])
    start=int(component_id.rsplit(':',1)[1])+1 if component_id and ':block:' in component_id else 0
    end=next((i for i in range(start,len(blocks)) if blocks[i].get('kind') in ('heading','ingredient_group','component_title','title')),len(blocks)) if start else len(blocks)
    for i in range(start,end):
        b=blocks[i]
        text=b['text']; kind=b.get('kind','unknown')
        # Inflection support is bounded; ingredient matcher retains derivative rules.
        match=ingredient_matches(term,[text]) or (term.endswith(' leaf') and ingredient_matches(term[:-5]+' leaves',[text]))
        if not match: continue
        negated=bool(re.search(r'\b(?:omit|without|do not use|don.t use|instead of)\b',text,re.I))
        role='ordinary'
        if term in ('banana leaf','corn husk') and re.search(r'wrapp|fold|square|tamale?',text,re.I): role='wrapper'
        optional=bool(re.search(r'\boptional\b|if (?:you like|desired|using)',text,re.I))
        alternative=bool(re.search(r'\bor\b|\balternativ|\binstead\b|substitut',text,re.I))
        group=None
        for j in range(i-1,-1,-1):
            if blocks[j].get('kind') in ('ingredient_group','component_title'):
                group=f"{recipe['id']}:block:{j}";break
        corroborated=kind in ('ingredient','instruction','method') and not negated
        evidence.append(dict(block_index=i,kind=kind,text=text,role=role,optional=optional,alternative=alternative,
                             component_id=group,corroborates=corroborated))
        if corroborated:
            roles.add(role)
            if optional: roles.add('optional')
            if alternative: roles.add('alternative')
            if (group and kind=='ingredient') or component_id: roles.add('component_use')
    if 'wrapper' in roles: roles.discard('ordinary')
    status='corroborated' if any(e['corroborates'] for e in evidence) else 'narrative_only' if evidence else 'unresolved_extraction_review'
    return dict(status=status,roles=sorted(roles),evidence=evidence,
                review_reason=None if status=='corroborated' else 'Inspect source for missing ingredient/method text or non-use; index disagreement is not a rejection.')


def entry_topics(entry):
    result=[]
    for label in entry.get('hierarchy',[])[:-1]:
        if term:=identity(label): result.append(term)
    # Flat indexes commonly encode "ingredient: recipe" / "ingredient, recipe".
    label=entry['label']
    if term:=identity(re.split(r'[:,]',label)[0]): result.append(term)
    return sorted(set(result))


def build_book(bundle, recipes, artifact_path):
    source=bundle['source']; book=source['book_id']
    recipes=[dict(r, _pages=pages(r), _headings=[(h,c,tokens(h)) for h,c in headings(r)]) for r in recipes if r['book_id']==book]
    if any(r['source'].get('sha256')!=source['sha256'] for r in recipes):
        raise ValueError('Recipe/index edition mismatch: '+book)
    by_recipe={r['id']:r for r in recipes}
    entries=bundle.get('entries',[])
    if 'indexes' in bundle: entries=[e for es in bundle['indexes'].values() for e in es]
    by_entry={e['id']:e for e in entries}
    links=[]
    for entry in entries:
        resolution=resolve_entry(entry,recipes)
        if book=='professional-chef' and source['sha256']=='afe929f37ce0b75e2928e4dacba5519a859227257c90330237bb8c736f8fa8eb':
            aliases={'Barbecued Beef':'Barbecued Beef Sandwich',
                'Warm Date Spice Cake with Dates, Butterscotch Sauce, and Cinnamon Ice Cream':'Warm Date Spice Cake with Butterscotch Sauce, and Cinnamon Ice Cream'}
            if entry['label'] in aliases:
                alias=aliases[entry['label']]
                resolution=resolve_entry(dict(entry,label=alias,hierarchy=[alias]),recipes)
                resolution['title_alias']=dict(original=entry['label'],body_title=alias,basis='Source-reviewed 2026-10-13 Chef master-list reconciliation; docs/decisions.md')
        target=resolution.get('recipe_id') or resolution.get('candidate_id')
        topics=entry_topics(entry) or [None]
        for topic in topics:
            validation=validate_ingredient(topic,by_recipe[target],resolution.get('component_id')) if target else dict(status='unresolved_recipe' if topic else 'not_an_ingredient',roles=[],evidence=[])
            links.append(dict(id='relationship_'+digest([book,entry['id'],topic])[:20],book_id=book,format=source.get('format','pdf'),
                source_entry_ids=[entry['id']],source_index=artifact_path,source_sha256=source['sha256'],
                source_text=entry['source_text'],hierarchy=entry.get('hierarchy',[entry['label']]),
                original_locators=locators(entry),source_entry=deepcopy(entry),
                ingredient=topic,relation='ingredient' if topic else 'recipe_lookup_or_topic',
                recipe_resolution=resolution,ingredient_validation=validation,
                provenance_kind='derived_from_book_index',candidate_locations=[d for ref in entry.get('page_references',[]) for d in ref.get('destinations',[])],verified_complete_recipe=False if target and by_recipe[target].get('_candidate') else None))
    # Typed related-topic edges are useful discovery, never primary ingredient
    # membership. Traverse source graph with cycle protection; keep each path.
    children=defaultdict(list)
    for e in entries: children[e.get('parent_id')].append(e['id'])
    direct=defaultdict(list)
    for link in links: direct[link['source_entry_ids'][0]].append(link)
    related=[]
    for entry in entries:
        topic=identity(entry['label'])
        if not topic: continue
        for ref in entry.get('cross_references',[]):
            queue=[(eid,[entry['id'],eid]) for eid in ref.get('target_entry_ids',[])]
            visited={entry['id']}
            while queue:
                eid,path=queue.pop(0)
                if eid in visited: continue
                visited.add(eid)
                for link in direct[eid]:
                    if link['recipe_resolution']['status'] not in ('resolved','candidate_resolved') or link['ingredient_validation']['status']!='corroborated': continue
                    row=deepcopy(link);row.update(id='relationship_'+digest([entry['id'],link['id'],ref['relation']])[:20],ingredient=topic,
                        relation='related_ingredient',source_entry_ids=path,cross_reference=deepcopy(ref))
                    row['ingredient_validation']=dict(status='related_corroborated',roles=['related_topic'],
                        corroborated_ingredient=link['ingredient'],evidence=link['ingredient_validation']['evidence'],
                        caveat='Book-authored related topic; does not establish queried ingredient composition or dietary membership.')
                    related.append(row)
                target=by_entry.get(eid,{})
                queue += [(x,path+[x]) for x in children[eid]]
                queue += [(x,path+[x]) for r in target.get('cross_references',[]) for x in r.get('target_entry_ids',[])]
    links += related
    return dict(book_id=book,source_sha256=source['sha256'],entry_count=len(entries),relationships=links,
        report=dict(entries=len(entries),relationships=len(links),recipe_resolution=dict(Counter(r['recipe_resolution']['status'] for r in links)),
            ingredient_validation=dict(Counter(r['ingredient_validation']['status'] for r in links))))


def serving_rows(books):
    return [{k:v for k,v in r.items() if k!='source_entry'} for book in books for r in book['relationships'] if r['recipe_resolution']['status']=='resolved'
            and r['ingredient_validation']['status'] in ('corroborated','related_corroborated')]


def load_relationships(data_dir, recipes):
    path=Path(data_dir)/'index-relationships.json'
    if not path.exists(): return []
    bundle=json.loads(path.read_text())
    if bundle.get('version')!=VERSION or bundle.get('recipe_fingerprint')!=recipe_fingerprint(recipes.values()):
        # A concurrent import must never serve stale IDs or stale validation.
        return []
    return [r for r in bundle['relationships'] if r['recipe_resolution']['status']=='resolved'
            and r['recipe_resolution']['recipe_id'] in recipes
            and r['ingredient_validation']['status'] in ('corroborated','related_corroborated')]


def epub_location_evidence(source_path, recipes, expected_sha256):
    """Read missing page-start context from the source DOM, never rewrite recipes.

    Retained blocks may first encounter a page marker after the recipe title,
    e.g. Prosciutto XO starts on 260 and continues onto 261. Walk source order.
    """
    from lxml import etree
    from .epub_index import read_package, printed_marker
    source_path=Path(source_path)
    if hashlib.sha256(source_path.read_bytes()).hexdigest()!=expected_sha256:
        raise ValueError('EPUB source hash changed')
    docs,_,_,_=read_package(source_path)
    lookup={(r['source'].get('member'),r['source'].get('anchor')):r['id'] for r in recipes}
    found=defaultdict(set)
    for member,doc in docs.items():
        page=None;active=None
        for event,node in etree.iterwalk(doc,events=('start','end')):
            if event=='start':
                marker=printed_marker(node)
                if not marker and (m:=re.fullmatch(r'(?:page_?|p)(\d+)',node.get('id',''))):marker=m[1]
                if marker:page=str(marker)
                rid=lookup.get((member,node.get('id')))
                if rid:active=(node,rid)
            elif active and node is active[0]:
                if page:found[active[1]].add(page)
                active=None
    return dict(found)
