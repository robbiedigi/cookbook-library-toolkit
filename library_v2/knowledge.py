"""Deterministic, offline knowledge migration. Source strings are never instructions.

Sections are archival/coherent read targets; legacy passages retain their exact
assessment scope. Reviewed menu children are additive and never replace prose.
No legacy service, importer, model or index is loaded by this module.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from functools import lru_cache

from .legacy import JsonlMapping, edition_id

SCHEMA_VERSION = 'library-v2-draft-1'
TOPICS = ('technique', 'ingredient_reference', 'food_science', 'history_culture',
          'personal_narrative', 'cooking_philosophy', 'menu_planning')
DEFAULT_BASELINE = Path(__file__).resolve().parents[1] / 'data/baseline-batch02'
MENU_REVIEW = Path(__file__).resolve().parents[1] / 'evals/library-v2/knowledge/menu-scopes.json'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _revision(item):
    item['revision'] = digest({k: v for k, v in item.items() if k != 'revision'})
    return item


def _citation(record, source, scope='block'):
    fmt = source.get('format') or 'unknown'
    labels = source.get('printed_pages')
    labels = labels if labels is not None else ([source['printed_page']] if source.get('printed_page') is not None else [])
    pages = source.get('pdf_pages')
    pages = pages if pages is not None else ([source['pdf_page']] if source.get('pdf_page') is not None else [])
    value = {'edition_id': edition_id(record), 'asset_sha256': source.get('sha256'),
             'book': source.get('book', record.get('book', '')),
             'author': source.get('author') or None,
             'format': fmt, 'printed_labels': [str(x) for x in labels],
             'pdf_file_pages': list(pages),
             'epub_member': source.get('member') if fmt == 'epub' else None,
             'epub_anchor': source.get('anchor') if fmt == 'epub' else None,
             'location_scope': scope}
    value['id'] = 'citation:' + digest(value)[:24]
    return value


@lru_cache(maxsize=1)
def _block_reviews():
    return json.loads(MENU_REVIEW.with_name('block-scopes.json').read_text())


@lru_cache(maxsize=1)
def _supporting_note_location_reviews():
    path = MENU_REVIEW.with_name('supporting-note-scopes.json')
    return json.loads(path.read_text())['reviews'] if path.exists() else []


def _block(record, raw, path, citations, archive_hash=None):
    archive_hash = archive_hash or digest(record)
    text = raw.get('text', '')
    original = raw.get('source_text', text)
    if not isinstance(original, str):
        raise ValueError('Source text must be a string')
    normalized = original != text
    span_path = path + ['source_text' if normalized else 'text']
    block_id = raw.get('id') or 'source:' + digest([archive_hash, span_path])[:32]
    sources = [raw.get('source') or record.get('source', {})] + raw.get('continued_sources', [])
    spans = []
    location_review = None
    for review in _supporting_note_location_reviews():
        if archive_hash == review['source_section_record_sha256'] and len(path) == 2 and path[0] == 'blocks':
            location_review = next((row for row in review['citation_overrides'] if row['block_index'] == path[1]), None)
            if location_review:
                break
    for source in sources:
        if location_review:
            source = dict(source, printed_page=location_review['printed_labels'][0],
                          printed_pages=list(location_review['printed_labels']))
        citation = _citation(record, source)
        citations[citation['id']] = citation
        spans.append({'source_record_sha256': archive_hash, 'source_block_id': block_id,
                      'legacy_path': span_path, 'start': 0, 'end': len(original),
                      'text_sha256': text_hash(original), 'citation_id': citation['id']})
    kind = {'image': 'figure', 'pdf_column': 'text', 'headnote': 'text', 'note': 'text'}.get(raw.get('kind'), raw.get('kind', 'text'))
    if kind not in {'text', 'table', 'figure'}:
        raise ValueError('Unmapped knowledge block kind: ' + str(kind))
    role = raw.get('kind') if raw.get('kind') in {'headnote', 'note'} else 'prose'
    if re.fullmatch(r'h[1-6]', raw.get('tag', '')):
        role = 'heading'
    block = {'id': block_id, 'kind': kind, 'role': role, 'text': text, 'basis': 'normalization' if normalized else 'source',
             'source_spans': spans, 'evidence_refs': ['accepted-extraction:' + archive_hash] if normalized else []}
    if location_review:
        block['evidence_refs'].append(location_review['review_ref'])
    if kind == 'table':
        block['rows'] = copy.deepcopy(raw.get('rows', []))
        # Headers require explicit retained row metadata; guessing from cell text is unsafe.
        block['header_rows'] = copy.deepcopy(raw.get('header_rows', []))
        reviewed = _block_reviews()['tables'].get(archive_hash, {}).get(str(path[-1]) if path else '', {})
        if reviewed:
            block['header_rows'] = list(reviewed['header_rows'])
            block['evidence_refs'].append(reviewed['review_ref'])
    if kind == 'figure':
        block['asset_refs'] = list(raw.get('assets', raw.get('asset_refs', [])))
    return block


def _base(record, blocks, citations):
    if not citations:
        citation = _citation(record, record.get('source', {}), 'section')
        citations[citation['id']] = citation
    uncertainties = [{'code': 'SOURCE_QUALIFICATION', 'message': str(flag), 'affects': [],
                      'action': 'inspect_source', 'citation_ids': list(citations)}
                     for flag in record.get('flags', [])]
    return {'schema_version': SCHEMA_VERSION, 'entity': 'knowledge', 'id': record['id'],
            'edition_id': edition_id(record), 'title': record.get('title', ''),
            'kind': 'passage', 'heading_path': list(record.get('headings', [])),
            'blocks': blocks, 'topics': [], 'uncertain_topics': [], 'topic_status': 'unknown',
            'related_recipe_ids': [], 'citations': list(citations.values()),
            'uncertainties': uncertainties,
            'provenance': {'source_record_sha256': digest(record), 'archive_record_sha256': digest(record), 'review_refs': []},
            'dependency_coverage': {'status': 'unknown', 'scope': 'Retained source knowledge; dependency applicability not reviewed.',
                                    'limitations': ['Source delivery does not certify a complete recipe or reviewed dependency coverage.']}}


def convert_knowledge(section, *, labels=None, passages=None):
    """Convert a complete section without unioning unlike passage assessments."""
    citations = {}
    source_hash = digest(section)
    blocks = [_block(section, b, ['blocks', i], citations, source_hash) for i, b in enumerate(section.get('blocks', []))]
    item = _base(section, blocks, citations)
    # A section can inherit only one identical assessed passage, never a union.
    exact = [p for p in passages or () if p.get('text') == section.get('text') and
             p.get('headings') == section.get('headings') and p.get('title') == section.get('title')]
    if len(exact) == 1:
        _apply_labels(item, exact[0], (labels or {}).get(exact[0]['id']))
    return _revision(item)


def _apply_labels(item, passage, row):
    if row is None:
        return
    expected = digest({'input': {'title': passage['title'], 'headings': passage['headings'],
                                'passage': passage['text']}, 'source': passage['source']})
    if row.get('input_sha256') != expected:
        item['topic_status'] = 'stale'
        return
    selected, uncertain = row.get('labels', []), row.get('uncertain_labels', [])
    if not set(selected + uncertain).issubset(TOPICS) or set(selected) & set(uncertain):
        raise ValueError('Invalid or overlapping knowledge topics')
    item['topics'] = [x for x in TOPICS if x in selected]
    item['uncertain_topics'] = [x for x in TOPICS if x in uncertain]
    item['topic_status'] = 'source_bound_assessment'


def convert_passage(passage, section, *, label=None):
    """Preserve an exact legacy passage and bind it to archived block strings.

    Full selected source blocks retain tables/figures. A boundary inside a source
    block uses the exact archived passage string, never a larger section fallback.
    """
    if passage['section_id'] != section['id']:
        raise ValueError('Passage parent mismatch')
    citations = {}
    indices = passage.get('block_indices', [])
    raw_blocks = [section['blocks'][i] for i in indices]
    joined = '\n\n'.join(b.get('text', '') for b in raw_blocks if b.get('text', ''))
    if joined == passage['text']:
        source_hash = digest(section)
        blocks = [_block(section, section['blocks'][i], ['blocks', i], citations, source_hash) for i in indices]
    else:
        # The accepted extractor can normalize whitespace at passage boundaries.
        # Its exact output is independently archived and hash bound, not rewritten.
        blocks = [_block(passage, {'text': passage['text'], 'kind': 'text', 'source': passage['source'],
                                  'continued_sources': passage.get('source_spans', [])[1:]}, [], citations)]
    item = _base(passage, blocks, citations)
    _apply_labels(item, passage, label)
    if joined != passage['text'] and any(b.get('kind') == 'table' for b in raw_blocks):
        item['uncertainties'].append({'code': 'TABLE_CONTEXT_REQUIRED',
            'message': 'This exact legacy excerpt intersects table material; read the parent section for complete rows and headers.',
            'affects': [section['id']], 'action': 'read_target', 'citation_ids': list(citations)})
    return _revision(item)


def retention_account(record):
    """Account every top-level and block field; archival fields remain hash bound."""
    mapped = {'id', 'title', 'headings', 'source', 'blocks', 'flags'}
    generated = {'embedding_text'}
    rows = [{'path': [key], 'disposition': 'mapped' if key in mapped else
             ('retired_generated_index' if key in generated else 'retained_archive'),
             'sha256': digest(value)} for key, value in record.items()]
    direct = {'text', 'kind', 'source', 'continued_sources', 'assets', 'rows', 'header_rows', 'id'}
    for i, block in enumerate(record.get('blocks', [])):
        rows.extend({'path': ['blocks', i, key], 'disposition': 'mapped' if key in direct else 'retained_archive',
                     'sha256': digest(value)} for key, value in block.items())
    source_fields = {'book_id', 'book', 'author', 'format', 'sha256', 'member', 'anchor', 'printed_page', 'pdf_page'}
    def source_rows(source, path):
        for key, value in source.items():
            rows.append({'path': path + [key], 'disposition': 'mapped' if key in source_fields else 'retained_archive',
                         'sha256': digest(value)})
    source_rows(record.get('source', {}), ['source'])
    for i, block in enumerate(record.get('blocks', [])):
        source_rows(block.get('source', {}), ['blocks', i, 'source'])
        for j, source in enumerate(block.get('continued_sources', [])):
            source_rows(source, ['blocks', i, 'continued_sources', j])
    return {'id': record['id'], 'archive_record_sha256': digest(record), 'fields': rows, 'unmapped': []}


def mapping(record, item, namespace):
    return {'old_namespace': namespace, 'old_id': record['id'], 'old_revision': digest(record),
            'status': 'exact', 'targets': [{'node': {'entity': 'knowledge', 'id': item['id']},
              'source_spans': [s for b in item['blocks'] for s in b['source_spans']]}], 'review_ref': None}


# Backward-compatible imports for existing extraction/evaluation callers.
from .chunking import text_ranges, iter_search_chunks


def chunk_context(passage):
    return {'parent_id': passage.get('section_id'), 'previous_id': passage.get('previous_id'),
            'next_id': passage.get('next_id'), 'legacy_char_range': [passage.get('char_start'), passage.get('char_end')]}


def _label_rows(path):
    """Stream the large records array with stdlib JSON, bounded by one row."""
    decoder = json.JSONDecoder()
    with Path(path).open() as f:
        buffer = ''
        while True:
            buffer += f.read(65536)
            match = re.search(r'"records"\s*:\s*\[', buffer)
            if match:
                buffer = buffer[match.end():]
                break
            if len(buffer) > 1_000_000:
                raise ValueError('Label records array not found in bounded metadata header')
            if not buffer:
                return
        while True:
            buffer = buffer.lstrip(' \r\n\t,')
            if buffer.startswith(']'):
                return
            try:
                row, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                more = f.read(65536)
                if not more:
                    raise ValueError('Truncated content label records')
                buffer += more
                continue
            yield row
            buffer = buffer[end:]


def _context_id(segments):
    return 'context:' + digest(['complete-source-blocks-v1', segments])[:32]


def convert_context(sections, spec):
    """Complete source-block context, or an explicitly reviewed logical range.

    Expanded context never inherits an exact excerpt's topic assessment. Original
    block identities and source citations survive, including cross-page spans.
    """
    citations, blocks, records = {}, [], {}
    for segment in spec['segments']:
        section = sections[segment['section_id']]
        source_hash = digest(section)
        if source_hash != segment['source_record_sha256']:
            raise ValueError('STALE_CONTEXT_SOURCE:' + section['id'])
        records[section['id']] = section
        i, start, end = segment['block_index'], segment['start'], segment['end']
        raw = section['blocks'][i]
        if not 0 <= start <= end <= len(raw['text']):
            raise ValueError('Context range is outside its source block')
        block = _block(section, raw, ['blocks', i], citations, source_hash)
        if (start, end) != (0, len(raw['text'])):
            if not spec.get('review_ref'):
                raise ValueError('Partial source block requires a scope review')
            if block['basis'] != 'source':
                raise ValueError('HELD_NORMALIZED_PARTIAL_CONTEXT:' + block['id'])
            if block['kind'] != 'text':
                raise ValueError('Partial table/figure context is not supported')
            block['text'] = raw['text'][start:end]
            block['source_spans'] = [dict(span, start=start, end=end,
                                          text_sha256=text_hash(block['text'])) for span in block['source_spans']]
        if spec.get('review_ref'):
            block['evidence_refs'].append(spec['review_ref'])
        blocks.append(block)
    first = next(iter(records.values()))
    item = _base(first, blocks, citations)
    item['id'] = spec['id']
    if spec.get('title_from'):
        title = spec['title_from']
        item['title'] = sections[title['section_id']]['blocks'][title['block_index']]['text'][title['start']:title['end']]
    item['provenance']['review_refs'] = [spec['review_ref']] if spec.get('review_ref') else []
    # Each contributing source keeps its qualifications. Do not imply that
    # delivering complete paragraphs verifies OCR or recipe completeness.
    flags = list(dict.fromkeys(flag for record in records.values() for flag in record.get('flags', [])))
    item['uncertainties'] = [{'code': 'SOURCE_QUALIFICATION', 'message': str(flag), 'affects': [],
                             'action': 'inspect_source', 'citation_ids': list(citations)} for flag in flags]
    return _revision(item)


class KnowledgeConverter:
    """Offline streaming sections/passages, with bounded source lookups."""
    def __init__(self, baseline_root=DEFAULT_BASELINE, *, ids=None, exclude_section_ids=()):
        self.data = Path(baseline_root) / 'runtime/data'
        self.ids = set(ids) if ids is not None else None
        self.exclude_section_ids = set(exclude_section_ids)
        self._context_specs = None
        self._read_targets = None
        self.sections = JsonlMapping(self.data / 'knowledge_sections.jsonl')
        self.labels = {r['passage_id']: {k: r[k] for k in ('input_sha256', 'labels', 'uncertain_labels')}
                       for r in _label_rows(self.data / 'content_labels.json')}

    def _plan_contexts(self, *, target_chars=4000):
        if self._context_specs is not None:
            return
        # Metadata only: no whole-corpus text or canonical content copy.
        selected = set(self.sections) if self.ids is None else {key for key in self.ids if key in self.sections}
        passage_meta = []
        with (self.data / 'knowledge_passages.jsonl').open() as stream:
            for line in stream:
                raw = json.loads(line)
                if self.ids is None or raw['id'] in self.ids or raw['section_id'] in self.ids:
                    passage_meta.append({key: raw.get(key) for key in ('id', 'section_id', 'block_indices', 'char_start', 'char_end')})
                    selected.add(raw['section_id'])
        specs, coverage = {}, {}
        covered = {}
        for review in _block_reviews().get('coherent_contexts', []):
            if not any(segment['section_id'] in selected for segment in review['segments']):
                continue
            segments = copy.deepcopy(review['segments'])
            for segment in segments:
                raw = self.sections[segment['section_id']]
                if digest(raw) != segment['source_record_sha256']:
                    raise ValueError('STALE_CONTEXT_REVIEW:' + segment['section_id'])
                covered.setdefault((segment['section_id'], segment['block_index']), []).append((segment['start'], segment['end']))
            spec = dict(review, segments=segments, id=_context_id(segments))
            specs[spec['id']] = spec
            for segment in segments:
                coverage.setdefault((segment['section_id'], segment['block_index']), []).append((segment['start'], segment['end'], spec['id']))
        for key in self.sections:
            if key not in selected:
                continue
            raw = self.sections[key]
            source_hash = digest(raw)
            pending, size = [], 0
            def flush():
                nonlocal pending, size
                if not pending:
                    return
                spec = {'id': _context_id(pending), 'segments': pending}
                # Residual fragments have explicit reviewed boundaries too.
                if any((part['section_id'], part['block_index']) in covered for part in pending):
                    spec['review_ref'] = 'v2-source-scope:thai-steamed-fish-continuation-2026-10-15'
                specs[spec['id']] = spec
                for part in pending:
                    coverage.setdefault((part['section_id'], part['block_index']), []).append((part['start'], part['end'], spec['id']))
                pending, size = [], 0
            for i, block in enumerate(raw['blocks']):
                end = len(block['text'])
                exclusions = sorted(covered.get((key, i), []))
                ranges, offset = [], 0
                for start, stop in exclusions:
                    if start > offset:
                        ranges.append((offset, start))
                    offset = max(offset, stop)
                if offset < end or not exclusions and end == 0:
                    ranges.append((offset, end))
                for start, stop in ranges:
                    length = stop - start
                    if pending and size + length + 2 > target_chars:
                        flush()
                    pending.append({'section_id': key, 'source_record_sha256': source_hash,
                                    'block_index': i, 'start': start, 'end': stop})
                    size += length + 2
                    if size >= target_chars:
                        flush()
            flush()
        targets = []
        for passage in passage_meta:
            key = passage['section_id']
            raw = self.sections[key]
            ids, source_blocks = [], []
            source_hash = digest(raw)
            for i in passage['block_indices']:
                candidates = coverage.get((key, i), [])
                for start, end, context_id in candidates:
                    # Only the explicitly reviewed cross-page block has range
                    # partitions. Its archived section is exactly one string, so
                    # legacy offsets establish which logical methods were hit.
                    if len(candidates) > 1 and len(raw['blocks']) == 1 and raw['text'] == raw['blocks'][0]['text']:
                        if max(start, passage['char_start']) >= min(end, passage['char_end']):
                            continue
                    if context_id not in ids:
                        ids.append(context_id)
                original = raw['blocks'][i]
                path = ['blocks', i, 'source_text' if original.get('source_text', original['text']) != original['text'] else 'text']
                source_blocks.append(original.get('id') or 'source:' + digest([source_hash, path])[:32])
            if not ids:
                raise ValueError('UNMAPPED_PASSAGE_CONTEXT:' + passage['id'])
            targets.append({'id': passage['id'], 'targets': [{'entity': 'knowledge', 'id': value} for value in ids],
                            'source_block_ids': source_blocks, 'parent_ids': [key], 'policy': 'complete-source-blocks-v1'})
        self._context_specs, self._read_targets = specs, targets

    def iter_contexts(self):
        self._plan_contexts()
        for spec in self._context_specs.values():
            yield convert_context(self.sections, spec)

    def iter_read_targets(self):
        self._plan_contexts()
        yield from self._read_targets

    def iter_sections(self):
        for key in self.sections.offsets:
            if key in self.exclude_section_ids or self.ids is not None and key not in self.ids:
                continue
            yield convert_knowledge(self.sections[key])

    def iter_passages(self):
        with (self.data / 'knowledge_passages.jsonl').open() as f:
            for line in f:
                passage = json.loads(line)
                if self.ids is not None and passage['id'] not in self.ids and passage['section_id'] not in self.ids:
                    continue
                yield convert_passage(passage, self.sections[passage['section_id']], label=self.labels.get(passage['id']))

    def iter_menus(self):
        reviews = json.loads(MENU_REVIEW.read_text())['sections']
        for key, review in reviews.items():
            if key in self.exclude_section_ids or self.ids is not None and key not in self.ids:
                continue
            yield from convert_menus(self.sections[key], review)

    def iter_chunks(self, *, max_chars=4000):
        # Archival sections remain readable but are not duplicate search hits.
        # Exact passage records are the assessment/search leaf scope. Reviewed
        # menu children are useful additional scopes, not replacements for prose.
        covered = set()
        with (self.data / 'knowledge_passages.jsonl').open() as f:
            for line in f:
                covered.add(json.loads(line)['section_id'])
        for item in self.iter_passages():
            yield from iter_search_chunks(item, max_chars=max_chars)
        for item in self.iter_menus():
            yield from iter_search_chunks(item, max_chars=max_chars)
        for item in self.iter_sections():
            if item['id'] not in covered:
                yield from iter_search_chunks(item, max_chars=max_chars)

    def iter_mappings(self):
        for key in self.sections:
            if key in self.exclude_section_ids or self.ids is not None and key not in self.ids:
                continue
            raw = self.sections[key]
            yield mapping(raw, convert_knowledge(raw), 'section')
        with (self.data / 'knowledge_passages.jsonl').open() as f:
            for line in f:
                raw = json.loads(line)
                if self.ids is not None and raw['id'] not in self.ids and raw['section_id'] not in self.ids:
                    continue
                yield mapping(raw, convert_passage(raw, self.sections[raw['section_id']], label=self.labels.get(raw['id'])), 'passage')

    def iter_context(self):
        with (self.data / 'knowledge_passages.jsonl').open() as f:
            for line in f:
                raw = json.loads(line)
                if self.ids is not None and raw['id'] not in self.ids and raw['section_id'] not in self.ids:
                    continue
                yield {'id': raw['id'], **chunk_context(raw),
                       'parent_scope': 'source_section',
                       'automatic_expansion': False}
        for key, context in _block_reviews()['cross_page_context'].items():
            if self.ids is None or key in self.ids:
                yield {'id': key, **context, 'automatic_expansion': False}
        reviews = json.loads(MENU_REVIEW.read_text())['sections']
        for key, review in reviews.items():
            if key in self.exclude_section_ids or self.ids is not None and key not in self.ids:
                continue
            for n, scope in enumerate(review['menus']):
                yield {'id': key + ':menu:' + str(n + 1), 'parent_id': key,
                       'block_indices': scope['block_indices'], 'review_ref': review['review_ref']}

    def iter_knowledge(self):
        yield from self.iter_sections()
        yield from self.iter_passages()
        yield from self.iter_menus()
        yield from self.iter_contexts()


def iter_knowledge(baseline_root=DEFAULT_BASELINE, ids=None, *, exclude_section_ids=()):
    yield from KnowledgeConverter(baseline_root, ids=ids, exclude_section_ids=exclude_section_ids).iter_knowledge()


def convert_menus(section, review, *, recipe_records=None):
    """Convert only source-inspected, hash-bound menu scopes, never title guesses.

    Review specifies complete block indices, dish/alternative slices and literal
    fields. Recipe links require an explicit source-reviewed recipe binding.
    """
    if digest(section) != review['source_record_sha256']:
        raise ValueError('STALE_MENU_SCOPE:' + section['id'])
    whole = convert_knowledge(section)
    for n, scope in enumerate(review['menus']):
        item = copy.deepcopy(whole)
        indices = scope['block_indices']
        if len(indices) != len(set(indices)) or indices != sorted(indices):
            raise ValueError('Menu blocks must preserve distinct source order')
        item['id'] = section['id'] + ':menu:' + str(n + 1)
        item['kind'] = 'menu'
        item['title'] = section['blocks'][scope['title_block']]['text'] if scope.get('title_block') is not None else section['title'] + ' ' + str(n + 1)
        item['blocks'] = [copy.deepcopy(whole['blocks'][i]) for i in indices]
        item['topics'] = ['menu_planning']
        item['topic_status'] = 'source_bound_assessment'
        item['provenance']['review_refs'] = [review['review_ref']]
        dishes = []
        for j, spec in enumerate(scope['dishes']):
            i = spec['block_index']
            if i not in indices:
                raise ValueError('Dish must belong to retained menu source blocks')
            text = section['blocks'][i]['text']
            if 'text_range' in spec:
                start, end = spec['text_range']
                if not 0 <= start < end <= len(text):
                    raise ValueError('Dish text range out of bounds')
                text = text[start:end]
            recipe_id = spec.get('recipe_id')
            if recipe_id is not None:
                if recipe_records is None or recipe_id not in recipe_records:
                    raise ValueError('Menu recipe link requires exact source target evidence')
                target = recipe_records[recipe_id]
                target_source = target.get('source', {})
                block_source = section['blocks'][i].get('source', section['source'])
                if not target_source.get('member') or not target_source.get('sha256'):
                    raise ValueError('Menu recipe link requires a nonempty source member and asset hash')
                if target_source.get('sha256') != block_source.get('sha256'):
                    raise ValueError('Menu recipe link crosses an unverified source asset')
                links = section['blocks'][i].get('links', [])
                if not any(link.get('path') == target_source.get('member') and
                           link.get('anchor') == target_source.get('anchor') and
                           (link.get('text') or '') in text for link in links):
                    raise ValueError('Menu recipe link is absent from the source dish block')
            dishes.append({'id': item['id'] + ':dish:' + str(j + 1), 'text': text,
                'recipe_id': recipe_id, 'role': spec.get('role'),
                'multiplier_text': spec.get('multiplier_text'), 'alternative_group': spec.get('alternative_group'),
                'source_block_ids': [whole['blocks'][i]['id']]})
        for field in ('guest_count_text', 'occasion'):
            value = scope.get(field)
            if value is not None and not any(value in b['text'] for b in item['blocks']):
                raise ValueError('Menu literal field is absent from its source scope')
        for dish in dishes:
            if dish['multiplier_text'] is not None and dish['multiplier_text'] not in dish['text']:
                raise ValueError('Menu multiplier is not literal source text')
        counts = {}
        for dish in dishes:
            group = dish['alternative_group']
            if group:
                counts[group] = counts.get(group, 0) + 1
        if any(count < 2 for count in counts.values()):
            raise ValueError('Alternative group requires at least two source options')
        notes = scope.get('preparation_note_indices', [])
        if not set(notes).issubset(indices):
            raise ValueError('Menu preparation notes must remain in scope')
        item['menu'] = {'occasion': scope.get('occasion'), 'guest_count_text': scope.get('guest_count_text'),
                        'dishes': dishes, 'preparation_note_block_ids': [whole['blocks'][i]['id'] for i in notes]}
        used = {s['citation_id'] for b in item['blocks'] for s in b['source_spans']}
        item['citations'] = [c for c in item['citations'] if c['id'] in used]
        yield _revision(item)
