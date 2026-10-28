"""Offline SQLite compiler and minimal immutable reader (stdlib only).

Compiler inputs are canonical records. Content validation is supplied by the shared
contracts module; persisted search text and projections are disposable outputs.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import sqlite3
import threading
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

MAX_RESPONSE_BYTES = 128 * 1024
SCHEMA_VERSION = 'library-v2-reader-2'


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def normalize(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c)).split())


def clean(value):
    """Remove archive/review diagnostics while preserving accepted text and basis."""
    if isinstance(value, list):
        return [clean(v) for v in value]
    if not isinstance(value, dict):
        return value
    audit = {'schema_version', 'revision', 'provenance', 'source_spans', 'evidence_refs',
             'bindings', 'review_ref', 'review_status', 'assembly_sha256',
             'selection', 'uncertain_topics', 'topic_status', 'variant_exclusions',
             'aliases', 'evidence_ref', 'asset_sha256', 'ingredient_occurrences'}
    result = {k: clean(v) for k, v in value.items() if k not in audit}
    if 'source_spans' in value and 'kind' in value and 'text' in value:
        result['citation_ids'] = list(dict.fromkeys(span['citation_id'] for span in value['source_spans']))
    if value.get('entity') == 'recipe':
        # General attached source notes become separately readable knowledge;
        # accepted cooking notes remain in the routine recipe projection.
        result['notes'] = [block for block in result.get('notes',[]) if block['role'] != 'context']
        cited = {citation_id for block in all_blocks(result) for citation_id in block.get('citation_ids',[])}
        result['citations'] = [citation for citation in result['citations'] if citation['location_scope']=='item' or citation['id'] in cited]
    return result


def all_blocks(item):
    for group in item.get('ingredient_groups', []):
        yield from group['blocks']
    for section in ('method_blocks', 'headnotes', 'notes', 'additional_context', 'blocks'):
        yield from item.get(section, [])


def block_text(block):
    text = block.get('text', '')
    if block.get('kind') == 'table':
        text += '\n' + '\n'.join('\t'.join(row) for row in block['rows'])
    return text


class CompileError(ValueError):
    pass


def _check_revision(record):
    body = {k: v for k, v in record.items() if k != 'revision'}
    if record.get('revision') != digest(body):
        raise CompileError('Stale canonical revision: ' + record.get('id', '?'))


def compile_database(path, *, books, recipes, knowledge, relationships=(), mappings=(), chunks=None, metadata=None, read_targets=()):
    """Create a new frozen snapshot, never modify an existing destination.

    All iterables are consumed once. The compiler uses SQLite for identity and
    binding checks rather than constructing an in-memory copy of the corpus.
    """
    from .contracts import validate_content, validate_output, validator
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.building')
    if temporary.exists():
        raise FileExistsError(temporary)
    db = sqlite3.connect(temporary)
    db.row_factory = sqlite3.Row
    counts = defaultdict(int)
    book_ids = set()
    try:
        db.executescript((Path(__file__).parent / 'schemas' / 'serving.sql').read_text())
        db.execute('CREATE TABLE read_targets(requested_id TEXT NOT NULL,ordinal INTEGER NOT NULL,target_entity TEXT NOT NULL,target_id TEXT NOT NULL,policy TEXT NOT NULL,PRIMARY KEY(requested_id,ordinal))')
        db.execute('CREATE TEMP TABLE block_coverage(entity TEXT NOT NULL,item_id TEXT NOT NULL,block_id TEXT NOT NULL,text_sha256 TEXT NOT NULL,PRIMARY KEY(entity,item_id,block_id,text_sha256)) WITHOUT ROWID')
        db.execute('CREATE TABLE delivery_parts(entity TEXT NOT NULL,item_id TEXT NOT NULL,ordinal INTEGER NOT NULL,content_json TEXT NOT NULL,PRIMARY KEY(entity,item_id,ordinal))')
        for book in books:
            validate_content(book)
            _check_revision(book)
            db.execute('INSERT INTO books VALUES(?,?,?,?)', (book['id'], book['revision'], book['title'], json_text(book)))
            for alias in {book['id'], book['title'], *book['aliases']}:
                db.execute('INSERT OR IGNORE INTO book_aliases VALUES(?,?)', (normalize(alias), book['id']))
            book_ids.add(book['id'])
            counts['books'] += 1
        for entity, records in [('recipe', recipes), ('knowledge', knowledge)]:
            for item in records:
                validate_content(item)
                _check_revision(item)
                if entity == 'recipe':
                    classification = item['classification']
                    db.execute('INSERT INTO recipes VALUES(?,?,?,?,?,?,?,?,?)', (item['id'], item['revision'], item['edition_id'], item['title'], normalize(item['title']), classification['kind'], classification['dish_type'], classification['status'], json_text(clean(item))))
                    for alias in {item['title'], *item['aliases']}:
                        db.execute('INSERT OR IGNORE INTO recipe_aliases VALUES(?,?)', (normalize(alias), item['id']))
                    for group in item['ingredient_groups']:
                        for block in group['blocks']:
                            source_row = db.execute('INSERT INTO ingredient_sources(recipe_id,block_id,text) VALUES(?,?,?)',(item['id'],block['id'],block['text'])).lastrowid
                            db.execute('INSERT INTO ingredient_fts(rowid,text) VALUES(?,?)',(source_row,ingredient_text(block['text'])))
                            annotation = block.get('ingredient', {})
                            occurrences = block.get('ingredient_occurrences')
                            if occurrences is None:
                                occurrences = [dict(start=0,end=len(block['text']),text=block['text'],
                                    optional=annotation.get('optional'),variant_scope=annotation.get('variant_scope'))]
                            previous_end = 0
                            for occurrence in occurrences:
                                start,end = occurrence['start'],occurrence['end']
                                if (start < previous_end or end <= start or end > len(block['text']) or
                                        occurrence['text'] != block['text'][start:end]):
                                    raise CompileError('Invalid ingredient occurrence source range: ' + block['id'])
                                previous_end = end
                                source = dict(text=occurrence['text'],block_id=block['id'],start=start,end=end)
                                occurrence_id = block['id'] + ':' + str(start) + ':' + str(end)
                                db.execute('INSERT INTO ingredient_occurrences VALUES(?,?,?,?,?,?,?,?,?)', (item['id'], occurrence_id, group['id'], annotation.get('identity'), annotation.get('role', 'unknown'), occurrence.get('optional'), occurrence.get('variant_scope'), json_text(source), source_row))
                    # Recipe discovery searches the target formula. Supporting
                    # notes and methods remain complete in reads and knowledge;
                    # a shared chapter must not dominate every recipe's rank.
                    db.execute('INSERT INTO recipe_fts VALUES(?,?,?,?,?)', (item['id'], item['title'],
                        '\n'.join(item['aliases']), '\n'.join(block_text(b) for g in item['ingredient_groups'] for b in g['blocks']),
                        '\n'.join(block_text(b) for b in item['headnotes'])))
                else:
                    if set(item['topics']) & set(item['uncertain_topics']):
                        raise CompileError('Selected and uncertain topics overlap: ' + item['id'])
                    if item['topics'] and item['topic_status'] != 'source_bound_assessment':
                        raise CompileError('Unbound positive topics: ' + item['id'])
                    db.execute('INSERT INTO knowledge_items VALUES(?,?,?,?,?,?)', (item['id'], item['revision'], item['edition_id'], item['kind'], item['title'], json_text(clean(item))))
                    db.executemany('INSERT INTO knowledge_topics VALUES(?,?)', [(item['id'], t) for t in item['topics']])
                    db.executemany('INSERT INTO knowledge_recipes VALUES(?,?)', [(item['id'], rid) for rid in item['related_recipe_ids']])
                    if chunks is None:
                        from .chunking import iter_search_chunks
                        for chunk in iter_search_chunks(item):
                            _insert_chunk(db, chunk)
                for block in all_blocks(item) if entity == 'knowledge' else ():
                    signature = digest({key:block[key] for key in ('kind','text','rows','header_rows','asset_refs') if key in block})
                    db.execute('INSERT OR IGNORE INTO block_coverage VALUES(?,?,?,?)',(entity,item['id'],block['id'],signature))
                for citation in item['citations']:
                    if citation['edition_id'] not in book_ids:
                        raise CompileError('Missing citation edition: ' + citation['edition_id'])
                    old = db.execute('SELECT content_json FROM citations WHERE id=?', (citation['id'],)).fetchone()
                    encoded = json_text(citation)
                    if old and old[0] != encoded:
                        raise CompileError('Conflicting citation ID: ' + citation['id'])
                    db.execute('INSERT OR IGNORE INTO citations VALUES(?,?,?)', (citation['id'], citation['edition_id'], encoded))
                for ordinal, part in enumerate(delivery_parts(item)):
                    validate_output(dict(schema_version=SCHEMA_VERSION,snapshot='compile-proof',next_cursor=None,page_index=0,complete=True,errors=[],roots=[],items=[part],relationships=[]))
                    db.execute('INSERT INTO delivery_parts VALUES(?,?,?,?)', (entity, item['id'], ordinal, json_text(part)))
                counts[entity] += 1
        if chunks is not None:
            for chunk in chunks:
                _insert_chunk(db, chunk)
        for row in db.execute("SELECT id,content_json FROM recipes WHERE json_array_length(content_json,'$.context_links')>0"):
            for link in json.loads(row['content_json'])['context_links']:
                if link['node']['entity'] != 'knowledge' or not _node(db,link['node']):
                    raise CompileError('Missing recipe context knowledge target: ' + row['id'])
        title_vocabulary = sorted({word for row in db.execute('SELECT normalized_title FROM recipes') for word in re.findall(r'[^\W_]+',row[0],re.UNICODE)})
        ordinals = defaultdict(int)
        choices = defaultdict(set)
        for relation in relationships:
            validate_content(relation)
            source = relation['from']
            target = relation['target']
            for node in [source] + ([target] if target else []):
                if not _node(db, node):
                    raise CompileError('Missing relationship endpoint: ' + json_text(node))
            if relation['review_status'] == 'accepted':
                for binding in relation['bindings']:
                    bound = _node(db, binding['node'])
                    if not bound or bound['revision'] != binding['revision']:
                        raise CompileError('Stale accepted relationship binding: ' + relation['id'])
                if not any(b['node'] == source for b in relation['bindings']):
                    raise CompileError('Missing source relationship binding: ' + relation['id'])
                if target and not any(b['node'] == target for b in relation['bindings']):
                    raise CompileError('Missing target relationship binding: ' + relation['id'])
            key = (source['entity'], source['id'])
            ordinal = ordinals[key]
            ordinals[key] += 1
            db.execute('INSERT INTO relationships VALUES(?,?,?,?,?,?,?,?,?,?,?)', (relation['id'], *key, ordinal, target['entity'] if target else None, target['id'] if target else None, relation['kind'], relation['disposition'], relation['choice_group'], relation['review_status'], json_text(relation)))
            if relation['disposition'] == 'choice':
                choices[(*key, relation['choice_group'])].add((target['entity'], target['id']))
        if any(len(options) < 2 for options in choices.values()):
            raise CompileError('Choice group requires at least two distinct options')
        for mapping in mappings:
            validate_content(mapping)
            for target in mapping['targets']:
                if not _node(db,target['node']):
                    raise CompileError('Missing mapping target: ' + mapping['old_id'])
            if mapping['status'] == 'exact' and len(mapping['targets']) != 1:
                raise CompileError('Exact mapping requires one target: ' + mapping['old_id'])
            # Mapping schema uses an explicit namespace/revision; never resolve
            # old chunks by an unversioned approximate text match.
            namespace = mapping.get('old_namespace', mapping.get('namespace'))
            db.execute('INSERT INTO id_mappings VALUES(?,?,?,?,?)', (namespace, mapping['old_id'], mapping['old_revision'], mapping['status'], json_text([dict(node=target['node']) for target in mapping['targets']])))
        for row in read_targets:
            validator('content.schema.json','read_target').validate(row)
            if set(row) - {'id','targets','source_block_ids','parent_ids','policy'}:
                raise CompileError('Unknown passage read-target fields: ' + row['id'])
            if row['policy'] != 'complete-source-blocks-v1' or not row['targets'] or not row['source_block_ids']:
                raise CompileError('Unsupported or empty passage read-target policy: ' + row['id'])
            if not _node(db,dict(entity='knowledge',id=row['id'])):
                raise CompileError('Missing passage read-target source: ' + row['id'])
            covered = set()
            for parent in row['parent_ids']:
                if not _node(db,dict(entity='knowledge',id=parent)):
                    raise CompileError('Missing passage context parent: ' + parent)
            unique = set()
            for ordinal,target in enumerate(row['targets']):
                identity=(target['entity'],target['id'])
                if identity in unique or target['entity'] != 'knowledge' or not _node(db,target):
                    raise CompileError('Missing or duplicate passage read target: ' + row['id'])
                unique.add(identity)
                covered.update(block[0] for block in db.execute('SELECT block_id FROM block_coverage WHERE entity=? AND item_id=?',identity))
                db.execute('INSERT INTO read_targets VALUES(?,?,?,?,?)',(row['id'],ordinal,*identity,row['policy']))
            if not set(row['source_block_ids']).issubset(covered):
                raise CompileError('Passage read targets do not cover declared source blocks: ' + row['id'])
        if db.execute('SELECT count(*) FROM ingredient_fts').fetchone()[0] != db.execute('SELECT count(*) FROM ingredient_sources').fetchone()[0]:
            raise CompileError('Ingredient candidate index row alignment failure')
        if db.execute('SELECT rowid FROM ingredient_sources EXCEPT SELECT rowid FROM ingredient_fts LIMIT 1').fetchone():
            raise CompileError('Ingredient candidate index identity alignment failure')
        db.execute("INSERT INTO ingredient_fts(ingredient_fts) VALUES('integrity-check')")
        manifest = dict(metadata or {})
        manifest.update(schema_version=SCHEMA_VERSION, counts=dict(counts), ingredient_policy=VERSION, title_vocabulary=title_vocabulary, ingredient_prefilter='fts5-source-block-v2')
        if 'snapshot' not in manifest:
            h = hashlib.sha256()
            for table in ('books','book_aliases','recipes','recipe_aliases','knowledge_items','knowledge_topics','knowledge_recipes','ingredient_sources','ingredient_occurrences','relationships','id_mappings','read_targets','delivery_parts','search_chunks','ingredient_fts_data','ingredient_fts_idx','ingredient_fts_docsize','ingredient_fts_config'):
                columns = len(db.execute(f'PRAGMA table_info({table})').fetchall())
                ordering = ','.join(str(index) for index in range(1,columns+1))
                h.update(table.encode())
                for row in db.execute(f'SELECT * FROM {table} ORDER BY {ordering}'):
                    h.update(json_text([{'binary_hex':value.hex()} if isinstance(value,bytes) else value for value in row]).encode())
            manifest['snapshot'] = h.hexdigest()
        db.executemany('INSERT INTO metadata VALUES(?,?)', [(key, json_text(value)) for key, value in sorted(manifest.items())])
        foreign = db.execute('PRAGMA foreign_key_check').fetchall()
        if foreign:
            raise CompileError('Foreign key violations: ' + repr([tuple(r) for r in foreign[:5]]))
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise CompileError('SQLite integrity failure')
        db.commit()
        db.close()
        os.replace(temporary, path)
        path.chmod(0o444)
        return manifest
    except BaseException:
        db.close()
        temporary.unlink(missing_ok=True)
        raise


def _insert_chunk(db, chunk):
    if hashlib.sha256(chunk['text'].encode()).hexdigest() != chunk['text_sha256']:
        raise CompileError('Stale search chunk hash: ' + chunk['chunk_id'])
    if not _node(db, {'entity': chunk['entity'], 'id': chunk['item_id']}):
        raise CompileError('Missing chunk parent: ' + chunk['chunk_id'])
    db.execute('INSERT INTO search_chunks(chunk_id,entity,item_id,ordinal,title,text,text_sha256) VALUES(?,?,?,?,?,?,?)', (chunk['chunk_id'], chunk['entity'], chunk['item_id'], chunk['ordinal'], chunk['title'], chunk['text'], chunk['text_sha256']))
    if chunk['entity'] == 'knowledge':
        db.execute('INSERT INTO knowledge_fts VALUES(?,?,?)', (chunk['chunk_id'], chunk['title'], chunk['text']))


def _node(db, node):
    table = {'recipe': 'recipes', 'knowledge': 'knowledge_items'}.get(node['entity'])
    if not table:
        return None
    row = db.execute(f'SELECT revision,content_json FROM {table} WHERE id=?', (node['id'],)).fetchone()
    return dict(row) if row else None


def delivery_parts(item, part_bytes=112*1024):
    from .reader import delivery_parts as compact_parts, ReaderError
    try:
        yield from compact_parts(item,part_bytes)
    except ReaderError as exc:
        raise CompileError(str(exc)) from exc


class Snapshot:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self._local = threading.local()
        self._connections = []
        self._lock = threading.Lock()
        self.metadata = {row[0]: json.loads(row[1]) for row in self.db.execute('SELECT * FROM metadata')}
        self.id = self.metadata['snapshot']

    @property
    def db(self):
        # SQLite/FTS requires a connection per concurrent thread. Sharing one
        # connection can produce SQLITE_MISUSE despite check_same_thread=False.
        if not getattr(self._local,'connection',None):
            connection = sqlite3.connect(self.path.as_uri() + '?mode=ro&immutable=1', uri=True, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            from .ingredient_identity import ingredient_occurrence_matches
            connection.create_function('ingredient_occurrence_matches',5,lambda *args:int(ingredient_occurrence_matches(*args)),deterministic=True)
            self._local.connection = connection
            with self._lock:
                self._connections.append(connection)
        return self._local.connection

    def close(self):
        with self._lock:
            for connection in self._connections:
                connection.close()
            self._connections.clear()
        self._local.connection = None

    def get(self, entity, item_id):
        row = _node(self.db, {'entity': entity, 'id': item_id})
        return json.loads(row['content_json']) if row else None


# Accepted batch02 bounded ingredient policy; copied verbatim except normalizer name.
"""Small, explicit culinary identity relationships, not dietary certification.

Children are narrower foods/cuts, not aliases: searching oxtail must not match
every beef recipe. Derived products stay separate and are opt-in group members.
Ambiguous 'fillet', 'mince', 'stock', 'short ribs' and product composition are not
assigned to an animal without source evidence. Keep this table reviewable.
"""
VERSION = 'ingredient-relationships-11'
CHILDREN = {
    'beef': ('oxtail', 'ox cheek', 'ox tongue', 'ribeye', 'rib eye', 'rib eye steak', 'sirloin steak',
             'rump steak', 'flank steak', 'skirt steak', 'porterhouse steak',
             't bone steak', 'chuck steak', 'chuck roast', 'beef short rib'),
    'pork': ('bacon', 'pancetta', 'guanciale', 'prosciutto', 'pork belly', 'pork shoulder'),
    'lamb': ('hogget', 'mutton'),
    'fish': ('salmon', 'trout', 'tuna', 'cod', 'halibut', 'haddock', 'sea bass',
             'snapper', 'sardine', 'mackerel', 'anchovy', 'arctic char', 'tilapia',
             'monkfish', 'hake', 'turbot', 'bream', 'sole', 'eel', 'whitefish', 'white fish',
             'swordfish', 'mullet', 'trevally', 'kingfish', 'kahawai', 'flounder',
             'grouper', 'herring', 'gurnard', 'whiting', 'john dory', 'sprat',
             'whitebait', 'mahi mahi', 'perch', 'barramundi', 'shark', 'tope', 'tench', 'carp', 'pike', 'skate', 'sturgeon', 'catfish'),
}
# Observed cookbook ingredient names. Keep these separate from finfish and do not
# guess ambiguous stems (for example "pipi russu" is a pepper, not a clam).
SEAFOOD_ADDITIONS = ('shellfish', 'cockle', 'abalone', 'langoustine', 'sea urchin', 'kina')
# These compounds do not imply the animal ingredient; the source may still
# contain it elsewhere. Applied only to named animal/seafood identities.
NON_ANIMAL_COMPOUNDS = ('oyster mushroom', 'king oyster mushroom', 'oyster cracker', 'fish mint', 'scallop squash',
    "lamb's lettuce", 'lamb’s lettuce', 'lambs lettuce', "lamb's quarter", 'lamb’s quarter',
    'chicken of the woods', 'chicken fried steak', 'chicken fried mushroom',
    'vegetarian bacon', 'vegan bacon',
    'vegetarian fish sauce', 'vegan fish sauce', 'vegetarian oyster sauce', 'vegan oyster sauce')
# Source explicitly identifies this clam name as shell color, not chocolate.
# Mask only for the ambiguous ingredient, preserving the seafood identity.
NON_INGREDIENT_HOMONYMS = {
    'chocolate': ('chocolate clam',),
    'beef': ('beef tomato', 'beefsteak tomato'),
}
# A source can elide the repeated noun: "mixed mushrooms, such as cremini
# (chestnut) and oyster". Restrict this mask to an explicit example list of
# known mushroom varieties; never suppress an oyster elsewhere in the row.
_MUSHROOM_VARIETY = r'(?:cremini|chestnut|button|shiitake|portobello|portabella|porcini|enoki|morel|chanterelle)s?'
MUSHROOM_EXAMPLE_PATTERN = (
    r'\bmushrooms?\s*,?\s+such as\s+'
    r'(?:' + _MUSHROOM_VARIETY + r'(?:\s*\(' + _MUSHROOM_VARIETY + r'\))?'
    r'\s*(?:,\s*(?:(?:and|or)\s+)?|(?:and|or)\s+))*'
    r'(?:king\s+)?oysters?\b(?!\s+sauce\b)'
)
PORK_ALTERNATIVES = ('turkey bacon', 'duck bacon', 'beef bacon',
                     *(f'{fish} bacon' for fish in CHILDREN['fish']))
ANIMAL_TERMS = set(CHILDREN) | {t for terms in CHILDREN.values() for t in terms} | {
    'chicken','seafood','shrimp','prawn','crab','lobster','crayfish','mussel','clam',
    'oyster','scallop','squid','octopus','cuttlefish','fish sauce','oyster sauce',
    'shrimp paste','prawn paste','bonito','katsuobushi','bottarga','fish roe','caviar', *SEAFOOD_ADDITIONS}
DERIVATIVE_SUFFIXES = ('stock','broth','bouillon','fat','dripping','tallow')


def related_terms(term):
    return (term, *CHILDREN.get(term, ()))


def derivative_terms(term):
    if term not in set(CHILDREN) | {'chicken'}: return ()
    return tuple(f'{child} {suffix}' for child in related_terms(term) for suffix in DERIVATIVE_SUFFIXES)


BEEF_TERMS = {'beef', *CHILDREN['beef'], *derivative_terms('beef')}
PORK_TERMS = {'pork', *CHILDREN['pork'], *derivative_terms('pork')}
for _animal in (*CHILDREN, 'chicken'):
    ANIMAL_TERMS.update(derivative_terms(_animal))

INGREDIENTS = ["fennel", "chicken", "polenta", "mushroom", "tomato", "eggplant", "chickpea", "cabbage", "leek", "potato", "carrot", "asparagus", "broccoli", "salmon", "shrimp", "pork", "beef", "lamb", "duck", "rice", "noodle", "pasta", "lentil", "bean", "lemon", "orange", "lime", "apple", "pear", "peach", "berry", "chocolate", "almond", "walnut", "peanut", "spinach", "arugula", "celery", "beet", "squash", "corn", "tofu", "egg", "yogurt", "cheese", "cream", "butter", "garlic", "ginger", "cucumber", "avocado", "mint", "basil", "cilantro", "parsley", "olive"]
INGREDIENTS += ["kale", "zucchini", "radish", "parmesan"]

# Bounded ingredient-identity exceptions, not a complete culinary ontology.
# Match derivatives by their own names, without treating them as the fruit.
INGREDIENT_DERIVATIVES = {
    "apple": ("apple cider vinegar", "apple cider", "apple juice concentrate", "apple juice",
              "apple butter", "apple sauce", "apple jam", "apple jelly", "apple brandy"),
    "apple cider": ("apple cider vinegar",),
    "fennel": ("fennel seed",),
    "beef": ("beef stock", "beef broth", "beef bouillon", "beef fat", "beef dripping", "beef tallow"),
    "chicken": ("chicken stock", "chicken broth", "chicken bouillon", "chicken fat"),
    "fish": ("fish sauce", "fish stock", "fish broth"),
    "cocoa": ("cocoa butter",),
    "cacao": ("cacao butter",),
}
INGREDIENT_ALIASES = {"aubergine": "eggplant", "garbanzo": "chickpea", "courgette": "zucchini",
                      "prawn": "shrimp", "fennel bulb": "fennel", "applesauce": "apple sauce",
                      "mayo": "mayonnaise", "artic char": "arctic char",
                      "rocket": "arugula", "beetroot": "beet", "capsicum": "bell pepper",
                      "finocchio": "fennel", "coriander leaf": "cilantro", "coriander leaves": "cilantro"}
INGREDIENT_GROUPS = {
    "group:chocolate": ("chocolate", "cocoa", "cacao"),
    "group:seafood": ("seafood", "fish", "salmon", "trout", "tuna", "cod", "halibut", "haddock",
        "sea bass", "snapper", "sardine", "mackerel", "anchovy", "shrimp", "crab", "lobster", "crayfish",
        "mussel", "clam", "oyster", "scallop", "squid", "octopus", "cuttlefish", "fish sauce",
        "fish stock", "fish broth", "oyster sauce", "shrimp paste", "prawn paste", "bonito", "katsuobushi",
        "bottarga", "fish roe", "caviar"),
}
for _parent in ('beef', 'chicken', 'pork', 'lamb', 'fish'):
    INGREDIENT_DERIVATIVES[_parent] = tuple(sorted(set(INGREDIENT_DERIVATIVES.get(_parent, ())) | set(derivative_terms(_parent))))
    INGREDIENT_GROUPS['group:' + _parent] = (_parent, *INGREDIENT_DERIVATIVES[_parent])
INGREDIENT_GROUPS['group:seafood'] += tuple(t for t in INGREDIENT_DERIVATIVES['fish'] if t not in INGREDIENT_GROUPS['group:seafood'])
INGREDIENT_GROUPS['group:seafood'] += SEAFOOD_ADDITIONS
INGREDIENT_TERMS = sorted(set(INGREDIENTS) | set(INGREDIENT_ALIASES)
                          | set(INGREDIENT_ALIASES.values()) | set(INGREDIENT_DERIVATIVES)
                          | set(CHILDREN) | {v for values in CHILDREN.values() for v in values}
                          | {v for values in INGREDIENT_GROUPS.values() for v in values}
                          | {v for values in NON_INGREDIENT_HOMONYMS.values() for v in values}
                          | {v for values in INGREDIENT_DERIVATIVES.values() for v in values},
                          key=lambda term: (-len(term), term))


def _ingredient_normalize(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def words(text):
    return re.findall(r"[a-z0-9]+", _ingredient_normalize(text))


@lru_cache(maxsize=8192)
def ingredient_text(text):
    return re.sub(r"\s+", " ", re.sub(r"[-‐‑–—]", " ", _ingredient_normalize(text))).strip()


@lru_cache(maxsize=512)
def ingredient_pattern(term):
    plurals = {"berry": "berr(?:y|ies)", "potato": "potato(?:es)?", "tomato": "tomato(?:es)?",
               "beef tomato": "beef tomato(?:es)?", "beefsteak tomato": "beefsteak tomato(?:es)?",
               "radish": "radish(?:es)?", "peach": "peach(?:es)?", "anchovy": "anchov(?:y|ies)"}
    return r"\b" + plurals.get(term, re.escape(term) + r"s?") + r"\b"


@lru_cache(maxsize=512)
def canonical_ingredient(term):
    term = ingredient_text(term)
    if term.startswith("group:"):
        if term not in INGREDIENT_GROUPS:
            raise ValueError("Unknown ingredient group; use library_status for the supported vocabulary.")
        return term
    for name in INGREDIENT_TERMS:
        if re.fullmatch(ingredient_pattern(name), term):
            return INGREDIENT_ALIASES.get(name, name)
    return term


@lru_cache(maxsize=512)
def ingredient_union(terms):
    return re.compile('|'.join(ingredient_pattern(t) for t in terms)) if terms else None


@lru_cache(maxsize=512)
def ingredient_forms(term):
    names = related_terms(term)
    return tuple(names) + tuple(alias for alias, target in INGREDIENT_ALIASES.items() if target in names)


def ingredient_matches(term, ingredients):
    term = canonical_ingredient(term)
    if term in INGREDIENT_GROUPS:
        candidates = tuple(form for member in INGREDIENT_GROUPS[term] for form in ingredient_forms(canonical_ingredient(member)))
        if not any(ingredient_union(candidates).search(ingredient_text(line)) for line in ingredients):
            return False
        return any(ingredient_matches(member, ingredients) for member in INGREDIENT_GROUPS[term])
    # Work per line: a multi-word ingredient must not be assembled across rows.
    for line in ingredients:
        text = ingredient_text(line)
        forms = ingredient_union(ingredient_forms(term))
        # Expand an explicitly coordinated ingredient name, not an unknown sauce
        # composition: 'beef or chicken stock' mentions both stock alternatives.
        shared = re.fullmatch(r'(beef|chicken|vegetable|fish|lamb|pork) (stock|broth)', term)
        shared_pattern = (r'\b'+shared[1]+r'\s+(?:or|/)\s+(?:beef|chicken|vegetable|fish|lamb|pork)\s+'+shared[2]+r'\b') if shared else None
        shared_match = bool(shared_pattern and re.search(shared_pattern,text))
        if not forms.search(text) and not shared_match: continue
        homonyms = ingredient_union(NON_INGREDIENT_HOMONYMS.get(term, ()))
        if homonyms:
            text = homonyms.sub(' ', text)
        if term in ANIMAL_TERMS:
            text = re.sub(MUSHROOM_EXAMPLE_PATTERN, ' ', text)
            compounds = NON_ANIMAL_COMPOUNDS + (PORK_ALTERNATIVES if term in PORK_TERMS else ())
            text = ingredient_union(compounds).sub(' ', text)
            text = re.sub(r'\b(?:vegan|vegetarian|plant based|mock)\s+(?:'+forms.pattern+r')', ' ', text)
            if shared_pattern:
                text = re.sub(r'\b(?:vegan|vegetarian|plant based|mock)\s+(?:'+shared_pattern+r')',' ',text)
        if term in BEEF_TERMS:
            text = re.sub(r'\b(?:pork|lamb|veal|venison|bison|buffalo|kangaroo)\s+(?:rib ?eye|sirloin|rump|flank|skirt|porterhouse|t bone|chuck|oxtail)(?:\s+(?:steak|roast))?s?\b', ' ', text)
        shared_match = bool(shared_pattern and re.search(shared_pattern,text))
        if term in CHILDREN or term == 'chicken':
            # Shared heads such as 'beef or chicken stock' name a stock option,
            # not meat. Group queries retain their derivative-level matches.
            text = re.sub(r'\b(?:beef|chicken|vegetable|fish|lamb|pork)\s+(?:or|/)\s+(?:beef|chicken|vegetable|fish|lamb|pork)\s+(?:stock|broth)\b', ' ', text)
        derivatives = ingredient_union(INGREDIENT_DERIVATIVES.get(term, ()))
        if derivatives: text = derivatives.sub(' ', text)
        if forms.search(text) or shared_match:
            return True
    return False



@lru_cache(maxsize=2048)
def ingredient_candidate_query(term):
    """Necessary candidate tokens only; never decide culinary identity here.

    Each accepted literal form must contain its longest normalized ASCII token.
    Prefixes conservatively include plurals and overmatches; the accepted matcher
    removes derivatives, homonyms, animal substitutes and other false positives.
    Non-ASCII/unfactorable forms or oversized expansions use exhaustive scanning.
    """
    term = canonical_ingredient(term)
    forms = ingredient_forms(term)
    if term in INGREDIENT_GROUPS:
        forms = tuple(form for member in INGREDIENT_GROUPS[term]
                      for form in ingredient_forms(canonical_ingredient(member)))
    prefixes = set()
    for form in forms:
        form = ingredient_text(form)
        if not form.isascii():
            return None
        tokens = re.findall(r'[a-z0-9]+',form)
        if not tokens:
            return None
        token = max(tokens,key=len)
        # These are the only non-prefix plural patterns in the accepted matcher.
        token = {'berry':'berr','anchovy':'anchov'}.get(token,token)
        if len(token)<2:
            return None
        prefixes.add(token)
    if not prefixes or len(prefixes)>256:
        return None
    query = ' OR '.join('"'+token+'"*' for token in sorted(prefixes))
    return query if len(query)<=8192 else None
