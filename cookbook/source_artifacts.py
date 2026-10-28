"""Narrow source-site watermark detection; never change source paths or assets."""
import re

VERSION = "source-artifacts-1"
_SITE = re.compile(r"(?:https?://)?(?:www\.)?ocean(?:of|o)?pdf\.com/?", re.I)


def is_source_artifact(text):
    """Recognize a standalone watermark, including spacing and URL variants.

    Do not remove a sentence, filename or another site's URL just because it
    mentions the domain. EPUB inline markup is already joined by text_content.
    """
    compact = re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", "", text)
    return bool(_SITE.fullmatch(compact))


def clean_recipe(record):
    """Return a corrected copy plus removed-block evidence for a private ledger."""
    from copy import deepcopy
    result = deepcopy(record)
    removed = [(i, b) for i, b in enumerate(record['blocks'])
               if is_source_artifact(b['text'])]
    if not removed:
        return result, []
    dropped = {i for i, _ in removed}
    # Removing real recipe structure or a cross-reference requires review.
    if any(b.get('kind') in {'title', 'ingredient', 'yield'} or b.get('links')
           for _, b in removed):
        raise ValueError('Unexpected watermark structure: ' + record['id'])
    kept = [i for i in range(len(record['blocks'])) if i not in dropped]
    positions = {old: new for new, old in enumerate(kept)}
    result['blocks'] = [result['blocks'][i] for i in kept]
    result['instructions'] = [b['text'] for b in result['blocks'] if b['kind'] == 'instruction']
    result['headnote'] = '\n'.join(b['text'] for b in result['blocks'] if b['kind'] == 'headnote')
    result['source_text'] = '\n'.join(b.get('source_text', b['text']) for b in result['blocks'])
    result['normalizations'] = [{**n, 'block_index': positions[n['block_index']]}
                                for n in result.get('normalizations', [])
                                if n['block_index'] not in dropped]
    result.setdefault('source_cleanups', []).append({
        'rule': 'standalone source-site watermark removed', 'version': VERSION,
        'removed_block_indices': sorted(dropped)})
    return result, [{'block_index': i, 'block': deepcopy(b)} for i, b in removed]
