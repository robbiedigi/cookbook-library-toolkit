"""Source-bound search chunks, independent of legacy import archives."""
import copy
import hashlib
from .contracts import canonical_json

def encoded(value):
    return canonical_json(value).encode("utf-8")

def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()

def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def text_ranges(text, max_chars=4000):
    """Lossless, deterministic paragraph/line/sentence-aware codepoint ranges."""
    if max_chars < 32:
        raise ValueError('max_chars must be at least 32')
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            floor = start + max_chars // 3
            for pattern in ('\n\n', '\n', '. ', ' '):
                at = text.rfind(pattern, floor, end)
                if at >= floor:
                    end = at + len(pattern)
                    break
        yield start, end
        start = end


def iter_search_chunks(item, *, max_chars=4000):
    """Bounded chunks; tables are atomic or explicitly held, never flattened.

    Stable item/block spans preserve source context independently of chunk IDs.
    A caller may inspect ``chunk_context`` for previous/next/parent read targets.
    """
    ordinal = 0
    for block in item['blocks']:
        text = block['text']
        if not text:
            continue
        ranges = [(0, len(text))] if block['kind'] == 'table' else text_ranges(text, max_chars)
        for start, end in ranges:
            spans, span_keys = [], set()
            for span in block['source_spans']:
                # Direct source spans have exact text offsets; non-source accepted
                # corrections retain complete evidence spans rather than invent offsets.
                if block['basis'] == 'source' and span['end'] - span['start'] == len(text):
                    s = dict(span, start=span['start'] + start, end=span['start'] + end,
                             text_sha256=text_hash(text[start:end]))
                else:
                    s = copy.deepcopy(span)
                key = encoded(s)
                if key not in span_keys:
                    span_keys.add(key)
                    spans.append(s)
            value = text[start:end]
            chunk = {'chunk_id': 'chunk:' + digest([item['id'], item['revision'], block['id'], start, end])[:32],
                   'entity': 'knowledge', 'item_id': item['id'], 'ordinal': ordinal,
                   'title': item['title'], 'text': value, 'text_sha256': text_hash(value), 'source_spans': spans}
            if block['kind'] == 'table' and len(encoded(chunk)) > 24 * 1024:
                raise ValueError('HELD_OVERSIZED_TABLE:' + block['id'])
            yield chunk
            ordinal += 1


