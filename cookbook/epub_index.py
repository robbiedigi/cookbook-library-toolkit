"""Retain publisher indexes as evidence, separate from recipe/ingredient search."""
import base64
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

from lxml import etree, html

from .importer import clean, resolve
from .source_artifacts import is_source_artifact

VERSION = 'epub-book-index-1'
INDEX_TITLE = re.compile(r'(?:general |subject |recipe )?index', re.I)
SEE = re.compile(r'\bsee(?P<also>\s+also)?\s+', re.I)
LOCATORS = re.compile(r'(?:,\s*|\s+)(\d{1,4}(?:\s*[-–—]\s*\d{1,4})?(?:\s*,\s*\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)*)\s*$')
PAGE_ANCHOR = re.compile(r'(?:page_?|p)\d+(?:[a-z]|_anchor)?',re.I)
# Observed publisher classes. Markup remains available when new classes appear.
LEVELS = {'primary': 0, 'main': 0, 'secondary': 1, 'sub': 1, 'tertiary': 2,
          'index': 0, 'indext': 0, 'index1': 1, 'indexs': 1, 'ind-main': 0, 'ind-main-sp': 0,
          'ind-sub': 1, 'ind-sub1': 2, 'index_main': 0, 'index_sub': 1,
          'first2_noindent': 0, 'first2_indent': 1,
          'IX': 0, 'IX1': 0, 'IXS': 1, 'indexmain': 0, 'indexmain1': 0, 'indexsub': 1, 'Index-1': 0, 'Index-2': 1}


def normalized(text):
    return ' '.join(re.findall(r'[^\W_]+', text.casefold()))


def read_package(path):
    """Inspect every declared XHTML member, including non-spine indexes."""
    with ZipFile(path) as archive:
        container = etree.fromstring(archive.read('META-INF/container.xml'), etree.XMLParser(resolve_entities=False))
        opf = container.xpath('//*[local-name()="rootfile"]/@full-path')[0]
        package = etree.fromstring(archive.read(opf), etree.XMLParser(resolve_entities=False))
        items = [(resolve(opf, n.get('href')), n.get('media-type', ''))
                 for n in package.xpath('//*[local-name()="manifest"]/*')]
        declared = {resolve(opf, n.get('href')) for n in package.xpath('//*[local-name()="reference"]')
                    if n.get('type') == 'index'}
        raw, docs = {}, {}
        for member, media in items:
            if media in ('application/xhtml+xml', 'text/html'):
                raw[member] = archive.read(member)
                docs[member] = html.fromstring(raw[member], parser=html.HTMLParser(encoding='utf-8'))
            elif media == 'application/x-dtbncx+xml':
                nav = etree.fromstring(archive.read(member), etree.XMLParser(resolve_entities=False))
                for point in nav.xpath('//*[local-name()="navPoint"]'):
                    labels = point.xpath('./*[local-name()="navLabel"]/*[local-name()="text"]/text()')
                    if any(INDEX_TITLE.fullmatch(clean(t)) for t in labels):
                        declared.update(resolve(member, h) for h in point.xpath('./*[local-name()="content"]/@src'))
        for member, doc in docs.items():
            for a in doc.xpath('//nav//a[@href]'):
                if INDEX_TITLE.fullmatch(clean(a.text_content())):
                    declared.add(resolve(member, a.get('href')))
        styles = {m: archive.read(m) for m, media in items if media == 'text/css'}
    missing = declared - set(docs)
    if missing:
        raise ValueError('Declared index member missing: ' + repr(sorted(missing)))
    return docs, raw, styles, declared


def entry_nodes(doc, levels=None):
    levels = LEVELS if levels is None else levels
    result = []
    for n in doc.xpath('//body//*[self::p or self::div or self::li or self::blockquote]'):
        if n.xpath('.//p|.//div|.//li'):
            continue
        classes = n.get('class', '').split()
        nested = n.xpath('ancestor::li') or (n.tag == 'li' and n.xpath('ancestor::ul'))
        text = clean(n.text_content())
        if (any(c in levels for c in classes) or nested) and text and not is_source_artifact(text):
            if text.lower().startswith(('note:', 'a note about', 'page numbers', 'the page numbers')):
                continue
            result.append(n)
    return result


def discover_indexes(docs, declared):
    found = {}
    for member, doc in docs.items():
        signals = []
        if member in declared: signals.append('package_or_navigation')
        if doc.xpath('//*[@role="doc-index"]') or any('index' in n.get('epub:type', '').split() for n in doc.iter() if n.tag in ('body','section','div','nav','ul')):
            signals.append('semantic_index_type')
        if re.search(r'(?:index|(?:_|-)ind(?:_|-)|idx)[^/]*\.(?:x?html?|htm)$', member, re.I):
            signals.append('member_name')
        if entry_nodes(doc) and any(INDEX_TITLE.fullmatch(clean(n.text_content())) for n in doc.xpath('//h1|//h2|//p')[:30]):
            signals.append('index_heading_and_entries')
        if signals: found[member] = signals
    return found


def printed_marker(node):
    if node is None: return None
    if node.get('role') == 'doc-pagebreak' or 'pagebreak' in node.get('epub:type', '').split():
        return node.get('title') or node.get('aria-label') or None
    match = re.fullmatch(r'page_?(\d+)', node.get('id', ''))
    return match[1] if match else None


def capture(path, book_id, title, author, recipes=(), expected_sha256=None):
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError('Index source edition differs from imported corpus: ' + book_id)
    if any(r.get('source',{}).get('sha256',digest)!=digest for r in recipes):
        raise ValueError('Recipe edition differs from index source: ' + book_id)
    docs, raw, styles, declared = read_package(path)
    selected = discover_indexes(docs, declared)
    if book_id.startswith('mastering-the-art-of-french-cooking-volume-'):
        for member in docs:
            if re.search(r'_epub_inda_r1\.htm$', member):
                selected[member] = ['source_reviewed_index_continuation']
    if book_id == 'recipetin-eats-dinner-nagi-maehashi':
        # Recipe and ingredient indexes occupy fixed-layout pages. Keep every
        # text run, image reference and stylesheet; do not invent row hierarchy.
        for page in range(364, 373):
            member = f'OEBPS/page{page:03}.xhtml'
            if member not in docs: raise ValueError('Reviewed fixed-layout index page missing: ' + member)
            selected[member] = ['source_reviewed_fixed_layout_index']
    levels = {**LEVELS, **{
        'every-grain-of-rice-simple-chinese-home-c-fuchsia-dunlop': {'calibre32': 0, 'calibre49': 1},
        'jamies-dinners-jamie-oliver': {'index1': 0, 'index3': 0, 'index2': 1, 'index4': 1},
        'liquid-intelligence-dave-arnold': {'ind': 0, 'ind-t': 0, 'ind1': 1},
        'mastering-the-art-of-french-cooking-volume-1-julia-child': {'nonindent': 0, 'indent': 1, 'indent1': 2},
        'mastering-the-art-of-french-cooking-volume-2-julia-child': {'qtertiary': 3},
        'the-art-of-simple-food-alice-waters': {'calibre9': 0, 'ind': 1},
        'the-food-lab-better-home-cooking-through-j-kenji-lopez-alt': {'ind': 0, 'indt': 0, 'ind1': 1},
        'the-meathead-method-meathead': {'index_p': 0, 'index_s': 1},
        'the-zuni-cafe-cookbook-judy-rodgers': {'index-level': 0, 'index-levelsp': 0, 'index-level-1': 1, 'index-level-2': 2},
        'the-wok-recipes-and-techniques-j-kenji-lopez-alt': {'int': 0, 'int1': 1},
        'plenty-yotam-ottolenghi': {'index1': 0, 'index2': 1},
        'la-son-roy-choi-tien-nguyen-natasha-phan': {'ind1': 0, 'ind2': 1},
        'meathead-rux-martin': {'Main': 0, 'Sub-1': 1},
        'the-curry-guy-bible-dan-toombs': {'ind1': 0, 'ind2': 1},
        'the-food-of-sichuan-fuchsia-dunlop': {'ind': 0, 'ind1': 1},
    }.get(book_id, {})}
    anchors = {}
    for member, doc in docs.items():
        anchors[member]=defaultdict(list)
        for n in doc.xpath('//*[@id]'):anchors[member][n.get('id')].append(n)
    source = dict(book_id=book_id, book=title, author=author, file=path.name, sha256=digest, format='epub')
    documents, entries, entry_anchors = [], [], {}
    for member, signals in selected.items():
        doc = docs[member]; tree = doc.getroottree(); stack = []; node_entries = {}
        for n in entry_nodes(doc, levels):
            text = clean(n.text_content()); classes = n.get('class', '').split()
            ancestors = n.xpath('ancestor::li')
            if n.tag == 'li': ancestors = [*ancestors, n]
            if ancestors:
                level = len(ancestors) - 1; basis = 'nested_list'
            else:
                level = next(levels[c] for c in classes if c in levels); basis = 'publisher_class_provisional'
            xpath = tree.getpath(n)
            eid = 'epub_index_' + hashlib.sha256(f'{digest}:{member}:{xpath}'.encode()).hexdigest()[:20]
            # Only remove numeric locator links from the derived label. Linked
            # dish names must remain labels. Raw text/markup are kept separately.
            label_node = deepcopy(n)
            for a in label_node.xpath('.//a'):
                if re.fullmatch(r'\d+(?:\.\d+)?(?:\s*[-–—]\s*\d+)?|photo', clean(a.text_content()), re.I):
                    a.drop_tree()
            label = SEE.split(clean(label_node.text_content()))[0]
            label = LOCATORS.sub('', label).rstrip(' ,.:;–—-')
            while stack and stack[-1]['level'] >= level: stack.pop()
            parent = stack[-1] if stack else None
            entry = dict(id=eid, label=label, normalized_label=normalized(label), level=level,
                         parent_id=parent['id'] if parent else None,
                         hierarchy=(parent['hierarchy'] if parent else [])+[label],
                         hierarchy_basis=basis, source_text=text, classes=classes,
                         source={**source, 'member': member, 'xpath': xpath, 'pdf_page': None},
                         links=[], printed_locators=[], cross_references=[],
                         flags=['orphan_subentry'] if level and not parent else [],
                         verified_recipe_membership=False)
            see = SEE.search(text)
            match = LOCATORS.search((text[:see.start()] if see else text).strip())
            if match:
                entry['printed_locators'].append(dict(source_text=match[1], printed_pages=None, pdf_page=None,
                    status='source_locator_text_not_mapped', locator_kind='unknown_text_or_photograph'))
            if book_id == 'mastering-the-art-of-french-cooking-volume-2-julia-child':
                # This cumulative index explicitly qualifies Volume I locators.
                # Unqualified numeric link labels can instead be chapter anchors
                # (e.g. 4.1); never reinterpret them as printed page numbers.
                volume_one = re.search(r'\bI:\s*([\d,\s–—-]+)', text)
                entry['printed_locators'] = []
                if volume_one:
                    entry['printed_locators'].append(dict(
                        source_text=volume_one[1].strip(' ,'), printed_pages=None, pdf_page=None,
                        volume=1, target_book_id='mastering-the-art-of-french-cooking-volume-1-julia-child',
                        status='cross_volume_locator_unresolved', locator_kind='unknown_text_or_photograph'))
                    entry['label'] = re.split(r',?\s*\bI:', text)[0].rstrip(' ,;')
                    entry['normalized_label'] = normalized(entry['label'])
                    entry['hierarchy'][-1] = entry['label']
                entry['index_scope'] = 'Volumes 1 and 2; Roman numeral I refers to Volume 1'
            if see:
                entry['cross_references'].append(dict(relation='see_also' if see['also'] else 'see',
                    source_text=text[see.end():], target_entry_ids=[], status='unresolved_text'))
            for a in n.xpath('.//a[@href]'):
                href=a.get('href'); dest=resolve(member,href); anchor=unquote(urlsplit(href).fragment)
                targets = anchors.get(dest, {}).get(anchor,[])
                target = targets[0] if len(targets)==1 else None
                status = ('external' if dest is None else 'missing_member' if dest not in docs else
                          'ambiguous_anchor' if len(targets)>1 else
                          'missing_anchor' if anchor and target is None else 'retained_destination')
                entry['links'].append(dict(href=href, text=clean(a.text_content()), member=dest, anchor=anchor,
                    printed_page=printed_marker(target), pdf_page=None, status=status,
                    destination_kind='page_marker' if printed_marker(target) is not None else
                                     'possible_page_anchor' if PAGE_ANCHOR.fullmatch(anchor) else 'source_anchor',
                    kind='index_reference' if dest in selected else 'source_reference',
                    locator_kind='photograph' if clean(a.text_content()).casefold()=='photo' else 'unknown_text_or_photograph',
                    source_markup=etree.tostring(a, encoding='unicode', with_tail=False),
                    source_anchor_recipe_candidate_ids=[], verified_recipe_ids=[]))
            entries.append(entry); stack.append(entry); node_entries[xpath]=eid
            for anchor in ([n.get('id')] if n.get('id') else [])+n.xpath('.//@id'):
                entry_anchors[(member,anchor)]=eid
        text_nodes = []
        for text in doc.xpath('//body//text()[normalize-space()]'):
            owner=text.getparent().getparent() if text.is_tail else text.getparent()
            lineage=[owner,*owner.iterancestors()]
            if any(n.tag in ('script','style') for n in lineage):continue
            block=next((n for n in lineage if n.tag in ('p','li','div','h1','h2','h3')),owner)
            watermark=is_source_artifact(clean(block.text_content()))
            eid=next((node_entries[tree.getpath(n)] for n in lineage if tree.getpath(n) in node_entries),None)
            text_nodes.append(dict(xpath=tree.getpath(text.getparent()),slot='tail' if text.is_tail else 'text',
                text=str(text),entry_id=eid,classification='excluded_source_watermark' if watermark else 'entry' if eid else 'context_or_unparsed'))
        linked_styles=[resolve(member,h) for h in doc.xpath('//link[@rel="stylesheet"]/@href')]
        assets=[]
        with ZipFile(path) as archive:
            for n in doc.xpath('//body//img[@src]'):
                asset=resolve(member,n.get('src'))
                exists=asset in archive.namelist()
                assets.append(dict(member=asset,alt=n.get('alt'),xpath=tree.getpath(n),
                    sha256=hashlib.sha256(archive.read(asset)).hexdigest() if exists else None,
                    status='source_asset_retained_not_transcribed' if exists else 'missing_asset'))
        documents.append(dict(member=member,discovery_signals=signals,sha256=hashlib.sha256(raw[member]).hexdigest(),
            raw_base64=base64.b64encode(raw[member]).decode(),text_nodes=text_nodes,assets=assets,
            stylesheets=[dict(member=m,sha256=hashlib.sha256(styles[m]).hexdigest(),raw_base64=base64.b64encode(styles[m]).decode())
                         for m in dict.fromkeys(linked_styles) if m in styles]))
    # Cross-reference targets are retained without recursive expansion. Page
    # starts never become recipe anchors, even when they sit inside a title.
    recipe_anchors=defaultdict(set)
    for r in recipes:
        for b in r['blocks']:
            if b.get('kind') not in ('title','heading','ingredient_group'):continue
            for anchor in b.get('anchors',[]):recipe_anchors[(b['path'],anchor)].add(r['id'])
    roots=defaultdict(list)
    for entry in entries:
        if entry['parent_id'] is None:roots[entry['normalized_label']].append(entry['id'])
    for entry in entries:
        for link in entry['links']:
            key=(link['member'],link['anchor'])
            if link['kind']=='index_reference':
                link['target_entry_id']=entry_anchors.get(key)
                if link['target_entry_id'] and entry['cross_references']:
                    entry['cross_references'][0]['target_entry_ids'].append(link['target_entry_id'])
                    entry['cross_references'][0]['status']='linked_source_target'
            elif link['destination_kind']=='source_anchor' and link['status']=='retained_destination' and link['locator_kind']!='photograph':
                link['source_anchor_recipe_candidate_ids']=sorted(recipe_anchors.get(key,()))
        for ref in entry['cross_references']:
            if ref['target_entry_ids']:continue
            ids=roots.get(normalized(ref['source_text'].rstrip('.')),[])
            ref['target_entry_ids']=ids
            ref['status']='unique_label_candidate' if len(ids)==1 else 'ambiguous_label' if ids else 'unresolved_text'
    counts=Counter(t['classification'] for d in documents for t in d['text_nodes'])
    report=dict(book_id=book_id,title=title,status='captured' if documents else 'no_index_found_in_epub',
        source_sha256=digest,documents=len(documents),entries=len(entries),
        links=sum(len(e['links']) for e in entries),cross_references=sum(len(e['cross_references']) for e in entries),
        text_nodes=dict(counts),index_members=list(selected),
        scanned_members=list(docs),unresolved_links=sum(l['status'].startswith('missing') or l['status']=='ambiguous_anchor' for e in entries for l in e['links']))
    if documents and not entries:report['status']='retained_unparsed_index'
    return dict(version=VERSION,source=source,documents=documents,entries=entries,report=report,
        limitations=['Index evidence does not establish primary ingredients, exclusions or recipe completeness.',
                     'Unmapped locators and provisional hierarchy remain explicit; no page-to-recipe expansion.',
                     'No index found means this supplied EPUB, not every edition of the book.'])


def verify_capture(bundle, path):
    """Check retained bytes, every text-node slot and every original entry."""
    path=Path(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==bundle['source']['sha256']
    ids={e['id'] for e in bundle['entries']}
    assert len(ids)==len(bundle['entries'])
    for e in bundle['entries']:
        assert not e['parent_id'] or e['parent_id'] in ids
        assert not e['verified_recipe_membership']
        assert all(not link['verified_recipe_ids'] for link in e['links'])
    checked=0;node_count=0
    with ZipFile(path) as archive:
        for document in bundle['documents']:
            raw=archive.read(document['member'])
            assert base64.b64decode(document['raw_base64'])==raw
            assert hashlib.sha256(raw).hexdigest()==document['sha256']
            doc=html.fromstring(raw,parser=html.HTMLParser(encoding='utf-8'));tree=doc.getroottree()
            expected=[]
            for text in doc.xpath('//body//text()[normalize-space()]'):
                owner=text.getparent().getparent() if text.is_tail else text.getparent()
                if any(n.tag in ('script','style') for n in [owner,*owner.iterancestors()]):continue
                expected.append((tree.getpath(text.getparent()),'tail' if text.is_tail else 'text',str(text)))
            assert expected==[(t['xpath'],t['slot'],t['text']) for t in document['text_nodes']]
            node_count+=len(expected)
            for e in bundle['entries']:
                if e['source']['member']!=document['member']:continue
                nodes=tree.xpath(e['source']['xpath']);assert len(nodes)==1
                assert clean(nodes[0].text_content())==e['source_text']
                checked+=1
            for style in document['stylesheets']:
                assert base64.b64decode(style['raw_base64'])==archive.read(style['member'])
            for asset in document['assets']:
                if asset['sha256']:assert hashlib.sha256(archive.read(asset['member'])).hexdigest()==asset['sha256']
    assert checked==len(bundle['entries'])
    return dict(book_id=bundle['source']['book_id'],entries_checked=checked,text_nodes_checked=node_count,
                original_members_and_styles_exact=True,source_hash_matches=True)


def save(bundle, data_dir):
    directory=Path(data_dir)/'book-indexes';directory.mkdir(parents=True,exist_ok=True)
    book_id=bundle['source']['book_id']
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*',book_id):raise ValueError('Unsafe book identifier')
    path=directory/(book_id+'.json')
    encoded=json.dumps(bundle,ensure_ascii=False,indent=2)+'\n'
    if path.exists() and path.read_text()!=encoded:raise ValueError('Index exists; build a new candidate snapshot: '+book_id)
    path.write_text(encoded)
    reports=[]
    for candidate in sorted(directory.glob('*.json')):
        if candidate.name == 'manifest.json': continue
        stored = json.loads(candidate.read_text())
        # Reviewed PDF indexes may share this evidence directory. Preserve them
        # byte-for-byte, but only EPUB captures belong in this EPUB manifest.
        stored_source = stored.get('source', {})
        if stored_source.get('format') == 'pdf' or (
                not stored_source.get('format')
                and str(stored_source.get('file', '')).lower().endswith('.pdf')):
            continue
        reports.append(stored['report'])
    manifest=dict(version=VERSION,books=reports,book_count=len(reports),
                  entries=sum(r['entries'] for r in reports),status_counts=dict(Counter(r['status'] for r in reports)))
    (directory/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return bundle['report']
