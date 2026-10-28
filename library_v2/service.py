"""Bounded four-tool evidence service over one immutable SQLite snapshot."""
from __future__ import annotations
import base64
import difflib
import hashlib
import hmac
import json
import re
from collections import deque
from .store import Snapshot, SCHEMA_VERSION, MAX_RESPONSE_BYTES, json_text, digest, normalize, clean, ingredient_matches, canonical_ingredient, INGREDIENT_GROUPS, ingredient_candidate_query, all_blocks
from .reader import source_reference, archive_policy

UNKNOWN_COVERAGE = dict(status='unknown', scope='No accepted bounded dependency review', limitations=[])
MAX_NODES = 128
MAX_EDGES = 512
MAX_DEPTH = 16
QUERY_STOP = frozenset('a an the and or with of for to in on by from something nice find me recipe recipes please want i my some that this is it have like can make would which'.split())


def card_sources(item):
    """Discovery locators; block-specific attribution is provided by full reads."""
    sources = []
    for citation in item['citations']:
        source = source_reference(citation)
        if source not in sources:
            sources.append(source)
    return sources


def qualification_codes(item):
    archived = archive_policy()['archive_only_qualifications']
    return sorted({v['code'] for v in item['uncertainties']
                   if dict(code=v['code'], message=v['message']) not in archived})


def error(code, message, item_id=None, retry='correct_input', **extra):
    return dict(code=code, message=message, id=item_id, retry=retry, **extra)


class RequestError(ValueError):
    def __init__(self, value):
        self.value = value
        super().__init__(value['message'])


class SnapshotService:
    def __init__(self, db_path, cursor_secret, *, max_response_bytes=MAX_RESPONSE_BYTES, ingredient_prefilter=True):
        if isinstance(cursor_secret, str):
            cursor_secret = cursor_secret.encode()
        if len(cursor_secret) < 32:
            raise ValueError('Cursor secret must contain at least 32 bytes')
        if not 16384 <= max_response_bytes <= MAX_RESPONSE_BYTES:
            raise ValueError('Response budget must be between 16 and 128 KiB')
        self.snapshot = Snapshot(db_path)
        if self.snapshot.metadata.get('schema_version') != SCHEMA_VERSION:
            self.snapshot.close()
            raise ValueError('Snapshot reader schema is unsupported; compile a new snapshot.')
        self.secret = cursor_secret
        self.max_bytes = max_response_bytes
        self.ingredient_prefilter = ingredient_prefilter and self.snapshot.metadata.get('ingredient_prefilter')=='fts5-source-block-v2'

    @property
    def db(self):
        return self.snapshot.db

    def close(self):
        self.snapshot.close()

    def _cursor(self, tool, args, position, page):
        body = dict(snapshot=self.snapshot.id, tool=tool, input=digest(args), position=position, page=page)
        encoded = base64.urlsafe_b64encode(json_text(body).encode()).decode().rstrip('=')
        signature = hmac.new(self.secret, encoded.encode(), hashlib.sha256).hexdigest()
        return encoded + '.' + signature

    def _position(self, tool, args, cursor):
        if not cursor:
            return 0, 0
        try:
            encoded, signature = cursor.split('.')
            expected = hmac.new(self.secret, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError('signature')
            body = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
            if body['snapshot'] != self.snapshot.id:
                raise RequestError(error('SNAPSHOT_UNAVAILABLE', 'Cursor snapshot is no longer served; restart the read.', retry='restart'))
            if body['tool'] != tool or body['input'] != digest(args):
                raise RequestError(error('CURSOR_INPUT_MISMATCH', 'Cursor belongs to different tool inputs.', retry='restart'))
            if type(body['position']) is not int or type(body['page']) is not int or min(body['position'], body['page']) < 0:
                raise ValueError('position')
            return body['position'], body['page']
        except RequestError:
            raise
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise RequestError(error('INVALID_CURSOR', 'Invalid authenticated continuation cursor.', retry='restart'))

    def _base(self, page=0):
        return dict(schema_version=SCHEMA_VERSION, snapshot=self.snapshot.id, next_cursor=None, page_index=page, complete=True, errors=[])

    def _validate(self, tool, args):
        from .contracts import validate_input
        try:
            validate_input(tool, args)
        except Exception as exc:
            raise RequestError(error('INVALID_ARGUMENT', str(exc)[:1000])) from exc

    def _finish(self, payload):
        if len(json_text(payload).encode()) > self.max_bytes:
            raise RequestError(error('CONTENT_TOO_LARGE', 'Response metadata exceeds the bounded envelope; request fewer roots or narrower filters.', retry='correct_input'))
        return payload

    def _books(self, values):
        ids = []
        for value in values:
            rows = self.db.execute('SELECT b.id,b.title,b.content_json FROM book_aliases a JOIN books b ON b.id=a.edition_id WHERE a.normalized_alias=? ORDER BY b.id', (normalize(value),)).fetchall()
            exact = self.db.execute('SELECT id,title,content_json FROM books WHERE id=?', (value,)).fetchone()
            if exact:
                rows = [exact]
            if not rows:
                raise RequestError(error('UNKNOWN_BOOK', 'Unknown book ID, title or alias.', value))
            if len(rows) != 1:
                candidates = [dict(id=r['id'], title=r['title'], edition_label=json.loads(r['content_json'])['edition_label']) for r in rows[:20]]
                raise RequestError(error('AMBIGUOUS_BOOK', 'Book name identifies multiple editions; choose an ID.', value, candidates=candidates))
            ids.append(rows[0]['id'])
        return list(dict.fromkeys(ids))

    @staticmethod
    def _in(column, values, conditions, params):
        if values:
            conditions.append(column + ' IN (' + ','.join('?' for _ in values) + ')')
            params.extend(values)

    @staticmethod
    def _fts(query):
        # Quoted token disjunction is data, never a user-controlled FTS grammar.
        terms = list(dict.fromkeys(re.findall(r'[^\W_]+', normalize(query), re.UNICODE)))
        # Keep a literal fallback for titles composed only of common words.
        terms = [term for term in terms if term not in QUERY_STOP] or terms
        return ' OR '.join('"' + t.replace('"', '""') + '"' for t in terms[:100])

    def search_recipes(self, query=None, filters=None, preferences=None, limit=20, cursor=None):
        args = dict(query=query, filters=filters or {}, preferences=preferences or [], limit=limit)
        payload = dict(self._base(), mode='ranked' if query else 'enumeration', total=0, items=[])
        try:
            self._validate('search_recipes', dict(args, cursor=cursor))
            if args['preferences'] and not query:
                raise RequestError(error('INVALID_ARGUMENT','Preferences require a ranked query.'))
            args['preferences'] = sorted(set(normalize(value) for value in args['preferences']))
            args['filters'] = self._effective_filters(args['filters'])
            position, page = self._position('search_recipes', args, cursor)
            payload['page_index'] = page
            conditions, params = [], []
            self._in('r.edition_id', self._books(args['filters'].get('books', [])), conditions, params)
            self._in('r.kind', args['filters'].get('kind', []), conditions, params)
            self._in('r.dish_type', args['filters'].get('dish_type', []), conditions, params)
            for field, negate in [('include_ingredients', False), ('exclude_ingredients', True)]:
                for spec in args['filters'].get(field, []):
                    if isinstance(spec, str):
                        spec = dict(name=spec)
                    term = canonical_ingredient(spec['name'])
                    if spec.get('match') == 'family':
                        group = 'group:' + term
                        if group not in INGREDIENT_GROUPS:
                            raise RequestError(error('UNSUPPORTED_POLICY', 'No accepted ingredient family policy for ' + term))
                        term = group
                    predicates = []
                    candidate_query = ingredient_candidate_query(term) if self.ingredient_prefilter else None
                    if candidate_query:
                        predicates.append('b.rowid IN (SELECT rowid FROM ingredient_fts WHERE ingredient_fts MATCH ?)')
                        params.append(candidate_query)
                    presence = spec.get('presence', 'any_variant_or_optional' if negate else 'required_only')
                    if presence == 'required_only':
                        predicates.extend(['o.optional=0','o.variant_scope IS NULL'])
                    # The index only narrows candidates; culinary identity is
                    # always decided by the unchanged accepted matcher last.
                    predicates.append("ingredient_occurrence_matches(?,b.text,json_extract(o.source_json,'$.start'),json_extract(o.source_json,'$.end'),?)")
                    params.extend([term,presence])
                    predicate = 'r.id IN (SELECT o.recipe_id FROM ingredient_occurrences o JOIN ingredient_sources b ON b.rowid=o.source_id WHERE ' + ' AND '.join(predicates) + ')'
                    conditions.append(('NOT ' if negate else '') + predicate)
            join = ''
            order_params = []
            order = 'r.id'
            if query:
                fts = self._fts(query)
                if not fts:
                    raise RequestError(error('INVALID_ARGUMENT', 'Query must contain a searchable word.'))
                join = ' JOIN recipe_fts ON recipe_fts.recipe_id=r.id'
                conditions.append('recipe_fts MATCH ?')
                params.append(fts)
                preference_terms = list(dict.fromkeys(term for preference in args['preferences'] for term in re.findall(r'[^\W_]+',preference,re.UNICODE)))[:100]
                preference_score = '+'.join('(instr(lower(recipe_fts.title || \' \' || recipe_fts.ingredients || \' \' || recipe_fts.headnotes),?)>0)' for _ in preference_terms)
                order = 'CASE WHEN r.normalized_title=? THEN 0 ELSE 1 END,'
                order_params = [normalize(query)]
                if preference_terms:
                    order += '(' + preference_score + ') DESC,'
                    order_params.extend(preference_terms)
                order += 'bm25(recipe_fts,0,8,8,4,1),r.id'
            where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
            payload['total'] = self.db.execute('SELECT count(*) FROM recipes r' + join + where, params).fetchone()[0]
            if query and payload['total'] == 0:
                corrected = self._correct_query(query)
                if corrected != query:
                    params[-1] = self._fts(corrected)
                    payload['total'] = self.db.execute('SELECT count(*) FROM recipes r' + join + where, params).fetchone()[0]
            rows = self.db.execute('SELECT r.content_json FROM recipes r' + join + where + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?', (*params, *order_params, limit + 1, position)).fetchall()
            for row in rows[:limit]:
                item = json.loads(row[0])
                card = dict(id=item['id'], title=item['title'], book_id=item['edition_id'], kind=item['classification']['kind'], dish_type=item['classification']['dish_type'], excerpt=self._excerpt(item), sources=card_sources(item), qualification_codes=qualification_codes(item))
                if len(json_text(dict(payload, items=payload['items']+[card])).encode()) > self.max_bytes - 2048:
                    break
                payload['items'].append(card)
            consumed = len(payload['items'])
            if rows and not consumed:
                raise RequestError(error('CONTENT_TOO_LARGE', 'Search card metadata exceeds envelope; read by ID.', json.loads(rows[0][0])['id']))
            if position + consumed < payload['total']:
                payload.update(complete=False, next_cursor=self._cursor('search_recipes', args, position+consumed, page+1))
            if query:
                payload['total'] = None
            return self._finish(payload)
        except RequestError as exc:
            payload['errors'].append(exc.value)
            payload['complete'] = False
            return self._finish(payload)

    def _effective_filters(self,filters):
        result = dict(filters)
        result['books'] = sorted(self._books(result.get('books',[])))
        for field in ('kind','dish_type'):
            result[field] = sorted(set(result.get(field,[])))
        for field in ('include_ingredients','exclude_ingredients'):
            specs = []
            for spec in result.get(field,[]):
                if isinstance(spec,str):
                    spec = dict(name=spec)
                try:
                    name = canonical_ingredient(spec['name'])
                except ValueError as exc:
                    raise RequestError(error('UNSUPPORTED_POLICY','No accepted ingredient policy for ' + spec['name'])) from exc
                specs.append(dict(name=name,match=spec.get('match','identity'),
                    presence=spec.get('presence','required_only' if field.startswith('include') else 'any_variant_or_optional')))
            result[field] = [json.loads(value) for value in sorted(set(json_text(spec) for spec in specs))]
        return result

    def _correct_query(self,query):
        # Bounded lexical typo recovery affects free text only. Ingredient hard
        # filters never use edit distance. Vocabulary is persisted at compile time.
        vocabulary = self.db.execute('SELECT value_json FROM metadata WHERE key=\'title_vocabulary\'').fetchone()
        if not vocabulary:
            return query
        words = json.loads(vocabulary[0])
        corrected = []
        for term in re.findall(r'[^\W_]+',normalize(query),re.UNICODE):
            if term in words or len(term)<3:
                corrected.append(term);continue
            matches = difflib.get_close_matches(term,words,n=2,cutoff=0.78)
            corrected.append(matches[0] if len(matches)==1 else term)
        return ' '.join(corrected)

    @staticmethod
    def _excerpt(item):
        from .store import all_blocks, block_text
        return '\n'.join(block_text(b) for b in all_blocks(item))[:700]

    def search_knowledge(self, query, books=None, topics=None, kinds=None, recipe_ids=None, limit=20, cursor=None):
        args = dict(query=query, books=books or [], topics=topics or [], kinds=kinds or [], recipe_ids=recipe_ids or [], limit=limit)
        payload = dict(self._base(), mode='ranked', total=0, items=[])
        try:
            self._validate('search_knowledge', dict(args, cursor=cursor))
            args['books'] = sorted(self._books(args['books']))
            for field in ('topics','kinds','recipe_ids'):
                args[field] = sorted(set(args[field]))
            position, page = self._position('search_knowledge', args, cursor)
            payload['page_index'] = page
            fts = self._fts(query)
            if not fts:
                raise RequestError(error('INVALID_ARGUMENT', 'Query must contain a searchable word.'))
            conditions, params = ['knowledge_fts MATCH ?'], [fts]
            self._in('k.edition_id', self._books(args['books']), conditions, params)
            self._in('k.kind', args['kinds'], conditions, params)
            for key, table, column in [('topics','knowledge_topics','topic'), ('recipe_ids','knowledge_recipes','recipe_id')]:
                values = args[key]
                if values:
                    conditions.append(f'EXISTS (SELECT 1 FROM {table} x WHERE x.item_id=k.id AND x.{column} IN (' + ','.join('?' for _ in values) + '))')
                    params.extend(values)
            # Rank IDs first and hydrate only the selected page. Carrying source
            # text through the complete window needlessly materializes it.
            source = ' FROM knowledge_fts JOIN search_chunks c ON c.chunk_id=knowledge_fts.chunk_id JOIN knowledge_items k ON k.id=c.item_id WHERE ' + ' AND '.join(conditions)
            # bm25 cannot be used as an aggregate directly in FTS5.
            ranked = 'SELECT k.id,c.chunk_id,bm25(knowledge_fts) AS score' + source
            cte = ('WITH matches AS MATERIALIZED (' + ranked + '), '
                   'best AS (SELECT *,row_number() OVER (PARTITION BY id ORDER BY score,chunk_id) rn FROM matches), '
                   'picked AS MATERIALIZED (SELECT id,chunk_id,score FROM best WHERE rn=1 ORDER BY score,id LIMIT ? OFFSET ?) ')
            payload['total'] = None
            rows = self.db.execute(cte + 'SELECT k.content_json,c.text FROM picked p '
                'JOIN knowledge_items k ON k.id=p.id JOIN search_chunks c ON c.chunk_id=p.chunk_id '
                'ORDER BY p.score,p.id', (*params,limit+1,position)).fetchall()
            for row in rows[:limit]:
                item = json.loads(row[0])
                card = dict(id=item['id'], title=item['title'], kind=item['kind'], heading_path=item['heading_path'], topics=item['topics'], excerpt=row[1][:700], sources=card_sources(item), related_recipe_ids=item['related_recipe_ids'], qualification_codes=qualification_codes(item))
                if len(json_text(dict(payload, items=payload['items']+[card])).encode()) > self.max_bytes-2048:
                    break
                payload['items'].append(card)
            consumed = len(payload['items'])
            if rows and not consumed:
                raise RequestError(error('CONTENT_TOO_LARGE', 'Knowledge card metadata exceeds envelope.'))
            # One lookahead row also detects continuation after byte-budget
            # trimming. Ranked mode does not expose an exhaustive total.
            if len(rows) > consumed:
                payload.update(complete=False,next_cursor=self._cursor('search_knowledge',args,position+consumed,page+1))
            return self._finish(payload)
        except RequestError as exc:
            payload['errors'].append(exc.value)
            payload['complete'] = False
            return self._finish(payload)

    def get_recipes(self, recipe_ids, include_dependencies=True, cursor=None):
        return self._batch('get_recipes', 'recipe', recipe_ids, include_dependencies, cursor)

    def get_passages(self, passage_ids, cursor=None):
        return self._batch('get_passages', 'knowledge', passage_ids, False, cursor)

    def _batch(self, tool, entity, requested, dependencies, cursor):
        key = 'recipe_ids' if entity == 'recipe' else 'passage_ids'
        args = {key: requested}
        if entity == 'recipe':
            args['include_dependencies'] = dependencies
        payload = dict(self._base(), roots=[], items=[], relationships=[])
        try:
            self._validate(tool,dict(args,cursor=cursor))
            position,page = self._position(tool,args,cursor)
            payload['page_index'] = page
            nodes, seen, roots, pending = [], set(), [], deque()
            structured_nodes = set()
            item_cache = {}
            def read_item(node):
                if node not in item_cache:
                    item_cache[node] = self.snapshot.get(*node)
                return item_cache[node]
            for item_id in requested:
                targets = []
                if tool == 'get_passages':
                    targets = [(r['target_entity'],r['target_id']) for r in self.db.execute('SELECT target_entity,target_id FROM read_targets WHERE requested_id=? ORDER BY ordinal',(item_id,))]
                if not targets:
                    targets = [(entity,item_id)]
                first = read_item(targets[0])
                if not first:
                    failure = self._reference_error(entity,item_id)
                    payload['roots'].append(dict(id=item_id,status='error',error=failure))
                    continue
                payload['roots'].append(dict(id=item_id,status='ok',error=None,delivered_as=[dict(entity=e,id=i) for e,i in targets]))
                for node in targets:
                    item = read_item(node)
                    if not item:
                        raise RequestError(error('STALE_REFERENCE','Compiled passage read target is unavailable.',item_id,retry='restart'))
                    if item.get('kind') == 'menu':
                        structured_nodes.add(node)
                    if node not in seen:
                        nodes.append(node);seen.add(node)
                    if node not in roots:
                        roots.append(node)
            if tool == 'get_passages':
                coverage = {}
                def covered_blocks(node):
                    if node not in coverage:
                        coverage[node] = {(block['id'],digest(block)) for block in all_blocks(read_item(node))}
                    return coverage[node]
                def compatible_evidence(left,right):
                    # Equal cooking text alone cannot substitute for a record
                    # with a distinct warning, applicability or review scope.
                    return all(read_item(left).get(key)==read_item(right).get(key)
                               for key in ('uncertainties','dependency_coverage'))
                # Every resolved read target can cover a smaller requested
                # section. Prefer the broadest target so replacements never name
                # an intermediate context which is itself suppressed.
                candidates = sorted(nodes,key=lambda node:(-len(covered_blocks(node)),nodes.index(node)))
                replacements = {}
                for node in nodes:
                    own = covered_blocks(node)
                    # Structured menu dishes/alternatives are additional source
                    # evidence; equal prose coverage alone cannot replace them.
                    if not own or node in structured_nodes:
                        continue
                    for candidate in candidates:
                        if candidate == node:
                            continue
                        other = covered_blocks(candidate)
                        if own.issubset(other) and compatible_evidence(node,candidate) and (len(other)>len(own) or nodes.index(candidate)<nodes.index(node)):
                            replacements[node] = candidate;break
                nodes = [node for node in nodes if node not in replacements]
                roots = [node for node in roots if node not in replacements]
                for root in payload['roots']:
                    if root.get('delivered_as'):
                        root['delivered_as'] = list(dict.fromkeys(replacements.get((n['entity'],n['id']),(n['entity'],n['id'])) for n in root['delivered_as']))
                        root['delivered_as'] = [dict(entity=e,id=i) for e,i in root['delivered_as']]
            if roots:
                # Root order precedes first-seen dependency order. Traverse each
                # root separately for cycle detection, while deduplicating output.
                pending.extend((root,0,(root,)) for root in roots)
                expanded, relation_ids = set(), set()
                required_graph = {}
                while pending:
                    node,depth,path = pending.popleft()
                    if node in expanded:
                        continue
                    expanded.add(node)
                    for row in self.db.execute('SELECT content_json FROM relationships WHERE source_entity=? AND source_id=? ORDER BY ordinal',node):
                        relation = json.loads(row[0])
                        target = relation['target']
                        if relation['id'] not in relation_ids:
                            if len(relation_ids) >= MAX_EDGES:
                                payload['errors'].append(error('DEPENDENCY_LIMIT','Dependency edge limit reached; fetch component IDs separately.',node[1],retry='inspect_targets'))
                                pending.clear(); break
                            relation_ids.add(relation['id'])
                            payload['relationships'].append({key:relation[key] for key in ('id','from','target','kind','disposition','application','choice_group')})
                        if relation['review_status'] != 'accepted':
                            payload['errors'].append(error('STALE_REVIEW','Dependency review is stale or unknown.',relation['id'],retry='inspect_targets'))
                            continue
                        if relation['disposition'] == 'unresolved':
                            payload['errors'].append(error('UNRESOLVED_REFERENCE',relation['application'],relation['id'],retry='inspect_targets'))
                        if not dependencies or relation['disposition'] != 'required':
                            continue
                        target_node = (target['entity'],target['id'])
                        required_graph.setdefault(node,[]).append(target_node)
                        if target_node in path:
                            payload['errors'].append(error('DEPENDENCY_CYCLE','Required dependency cycle detected.',target['id'],retry='inspect_targets'))
                            continue
                        if depth >= MAX_DEPTH or (target_node not in seen and len(seen)>=MAX_NODES):
                            payload['errors'].append(error('DEPENDENCY_LIMIT','Dependency graph bound reached; fetch the target separately.',target['id'],retry='inspect_targets'))
                            continue
                        item = read_item(target_node)
                        if not item:
                            payload['errors'].append(self._reference_error(*target_node)); continue
                        if target_node not in seen:
                            nodes.append(target_node); seen.add(target_node)
                            pending.append((target_node,depth+1,path+(target_node,)))
                # Check the union graph too: multiple requested roots can cause
                # first-path traversal dedupe to conceal a cycle across roots.
                visiting, visited = set(), set()
                def visit(current):
                    if current in visiting:
                        if not any(e['code']=='DEPENDENCY_CYCLE' and e['id']==current[1] for e in payload['errors']):
                            payload['errors'].append(error('DEPENDENCY_CYCLE','Required dependency cycle detected.',current[1],retry='inspect_targets'))
                        return
                    if current in visited:
                        return
                    visiting.add(current)
                    for target in required_graph.get(current,[]):
                        visit(target)
                    visiting.remove(current);visited.add(current)
                for root in roots:
                    visit(root)
            # One deterministic event stream delivers metadata and content once.
            # Arrays on each envelope contain only events on that page; callers
            # concatenate them in page order. A cursor binds the entire request.
            metadata_events = [(key,value) for key in ('roots','relationships','errors') for value in payload[key]]
            payload = dict(self._base(page),roots=[],items=[],relationships=[])
            def events():
                for key,value in metadata_events:
                    yield key,None,value
                for node in nodes:
                    for row in self.db.execute('SELECT content_json FROM delivery_parts WHERE entity=? AND item_id=? ORDER BY ordinal',node):
                        yield 'items',row[0],None
            index = 0
            has_more = False
            emitted = 0
            used_bytes = len(json_text(payload).encode())
            # Encode each event once; never serialize the growing envelope in
            # the append loop. The input-bound cursor is under the schema's
            # 4096-character ceiling and replaces the four-byte null placeholder.
            for key,encoded,value in events():
                if index < position:
                    index += 1;continue
                if encoded is None:
                    encoded = json_text(value)
                cost = len(encoded.encode()) + (1 if payload[key] else 0)
                if used_bytes + cost > self.max_bytes-4096:
                    has_more = True;break
                if value is None:
                    value = json.loads(encoded)
                payload[key].append(value)
                used_bytes += cost
                index += 1
                emitted += 1
            if has_more:
                if not emitted:
                    raise RequestError(error('CONTENT_TOO_LARGE','One delivery event exceeds the bounded envelope; request a narrower source item.'))
                payload.update(complete=False,next_cursor=self._cursor(tool,args,index,page+1))
            # complete says delivery only; dependency failures/coverage remain explicit.
            return self._finish(payload)
        except RequestError as exc:
            failure = dict(self._base(),roots=[],items=[],relationships=[])
            failure.update(complete=False,errors=[exc.value])
            return self._finish(failure)

    def _reference_error(self,entity,item_id):
        rows = self.db.execute('SELECT status,targets_json FROM id_mappings WHERE old_id=? AND old_namespace IN (?,?) ORDER BY old_revision',(item_id,entity,'passage' if entity=='knowledge' else entity)).fetchall()
        if rows:
            targets = {json_text(target) for row in rows for target in json.loads(row['targets_json'])}
            return error('AMBIGUOUS_REFERENCE' if len(targets)>1 else 'STALE_REFERENCE','Legacy reference requires an explicit versioned mapping; search again.',item_id,retry='inspect_targets')
        return error('NOT_FOUND','No item with this exact ID in the serving snapshot.',item_id,retry='correct_input')
