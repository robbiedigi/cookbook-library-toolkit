"""Identity masks over a source block, with matches bound to source ranges.

The accepted registry lives in store; this adapter adds coordinates, not foods
or aliases. Source strings are data. No converter, model, corpus or database is
loaded here. Strict presence never borrows an unknown continuation's scope.
"""
from dataclasses import dataclass
from functools import lru_cache
import re
import unicodedata

MAX_CACHED_SOURCE_CHARS = 8192
CACHED_SOURCE_QUERIES = 32
PRESENCES = {"required_only", "any_variant_or_optional"}


@dataclass(frozen=True)
class SourceMatch:
    start: int
    end: int
    token_spans: tuple


def normalize_with_offsets(source):
    """Match the accepted normalizer, retaining half-open Unicode coordinates."""
    chars, starts, ends = [], [], []
    pending_space = None
    for index, original in enumerate(source):
        normalized = unicodedata.normalize("NFKD", original.casefold())
        for char in normalized:
            if unicodedata.combining(char):
                continue
            if char in "-‐‑–—" or char.isspace():
                if chars:
                    pending_space = (pending_space[0] if pending_space else index, index + 1)
                continue
            if pending_space is not None:
                chars.append(" ")
                starts.append(pending_space[0])
                ends.append(pending_space[1])
                pending_space = None
            chars.append(char)
            starts.append(index)
            ends.append(index + 1)
    return "".join(chars), tuple(starts), tuple(ends)


def _mask(pattern, text):
    if not pattern:
        return text
    # Equal length replacement preserves every normalized/source coordinate.
    return re.sub(pattern, lambda match: " " * (match.end() - match.start()), text)


def _normalized_matches(term, normalized, policy):
    term = policy.canonical_ingredient(term)
    if term in policy.INGREDIENT_GROUPS:
        matches = set()
        for member in policy.INGREDIENT_GROUPS[term]:
            matches.update(_normalized_matches(member, normalized, policy))
        return matches
    forms = policy.ingredient_union(policy.ingredient_forms(term))
    shared = re.fullmatch(r"(beef|chicken|vegetable|fish|lamb|pork) (stock|broth)", term)
    shared_pattern = (r"\b" + shared[1] + r"\s+(?:or|/)\s+(?:beef|chicken|vegetable|fish|lamb|pork)\s+" + shared[2] + r"\b") if shared else None
    if not forms.search(normalized) and not (shared_pattern and re.search(shared_pattern, normalized)):
        return set()
    text = _mask(policy.ingredient_union(policy.NON_INGREDIENT_HOMONYMS.get(term, ())), normalized)
    if term in policy.ANIMAL_TERMS:
        text = _mask(policy.MUSHROOM_EXAMPLE_PATTERN, text)
        compounds = policy.NON_ANIMAL_COMPOUNDS + (policy.PORK_ALTERNATIVES if term in policy.PORK_TERMS else ())
        text = _mask(policy.ingredient_union(compounds), text)
        text = _mask(r"\b(?:vegan|vegetarian|plant based|mock)\s+(?:" + forms.pattern + r")", text)
        if shared_pattern:
            text = _mask(r"\b(?:vegan|vegetarian|plant based|mock)\s+(?:" + shared_pattern + r")", text)
    if term in policy.BEEF_TERMS:
        text = _mask(r"\b(?:pork|lamb|veal|venison|bison|buffalo|kangaroo)\s+(?:rib ?eye|sirloin|rump|flank|skirt|porterhouse|t bone|chuck|oxtail)(?:\s+(?:steak|roast))?s?\b", text)
    if term in policy.CHILDREN or term == "chicken":
        text = _mask(r"\b(?:beef|chicken|vegetable|fish|lamb|pork)\s+(?:or|/)\s+(?:beef|chicken|vegetable|fish|lamb|pork)\s+(?:stock|broth)\b", text)
    shared_matches = {match.span() for match in re.finditer(shared_pattern, text)} if shared_pattern else set()
    text = _mask(policy.ingredient_union(policy.INGREDIENT_DERIVATIVES.get(term, ())), text)
    return {match.span() for match in forms.finditer(text)} | shared_matches


def _build_data(term, source):
    # Lazy import permits store to register this function after its policy loads.
    from . import store as policy
    normalized, starts, ends = normalize_with_offsets(source)
    result = []
    for begin, end in sorted(_normalized_matches(term, normalized, policy)):
        tokens = []
        for token in re.finditer(r"\w+", normalized[begin:end]):
            # A conjunction alone is not ingredient evidence for a shared head.
            if token.group() == "or":
                continue
            token_start, token_end = begin + token.start(), begin + token.end()
            tokens.append((starts[token_start], ends[token_end - 1]))
        result.append(SourceMatch(starts[begin], ends[end - 1], tuple(tokens)))
    return tuple(result)


@lru_cache(maxsize=CACHED_SOURCE_QUERIES)
def _cached_data(term, source):
    return _build_data(term, source)


def ingredient_match_spans(term, source_text):
    """Return surviving identity matches in original source codepoint ranges."""
    if not isinstance(term, str) or not isinstance(source_text, str):
        raise ValueError("Ingredient identity needs a term and source string")
    return (_cached_data(term, source_text) if len(source_text) <= MAX_CACHED_SOURCE_CHARS
            else _build_data(term, source_text))


def ingredient_occurrence_matches(term, source_text, start, end, presence="any_variant_or_optional"):
    """Apply full-source masks, then require evidence in the selected occurrence.

    Discovery permits an identity spanning neutral physical wraps. Strict
    presence requires the complete surviving identity to fit the independently
    eligible occurrence; SQL must additionally require optional=0 and no variant.
    An optional fruit elsewhere never validates a required derivative occurrence.
    """
    if type(start) is not int or type(end) is not int or not isinstance(source_text, str) or not 0 <= start < end <= len(source_text):
        raise ValueError("Invalid ingredient occurrence source range")
    if presence not in PRESENCES:
        raise ValueError("Unknown ingredient presence policy")
    for match in ingredient_match_spans(term, source_text):
        if presence == "required_only":
            if start <= match.start and match.end <= end:
                return True
        elif any(token_start < end and token_end > start for token_start, token_end in match.token_spans):
            return True
    return False
