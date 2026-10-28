"""Rebuildable, source-bound discovery hints; never edits cookbook text or coarse course."""
import hashlib
import copy
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

VERSION = "recipe-discovery-labels-1"
from .recipe_annotation import VERSION as SEMANTIC_VERSION, KINDS, FORMS


def fingerprint(recipe):
    # Bind to the complete canonical record, including source corrections.
    return hashlib.sha256(json.dumps(recipe, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def unknown(reason="unassessed"):
    return dict(preparation_kind="unknown", dish_form="unknown", meal_role_cues=[],
                basis="unknown", review_status=reason, evidence=[])


def classify(recipe):
    """Conservative title-form rules plus explicit author-stated meal-role cues.

    These are provisional derived labels, not semantic review or source facts.
    Ambiguous titles deliberately remain unknown. No default main/side inference.
    """
    title = recipe["title"]
    text = "".join(c for c in unicodedata.normalize("NFKD", title.casefold()) if not unicodedata.combining(c))
    head = re.split(r"\b(?:with|in|on)\b|\s[–—:]\s", text, maxsplit=1)[0].strip()
    result = unknown("provisional")
    result["basis"] = "bounded source-title rules; explicit meal-role cues"
    match = None
    if not re.search(r"\bor\b|\b(?:rolls?|dumplings?|batter|frosting|filling|powder)\s*$", head):
        # A sandwich containing slaw is still a sandwich. A salad with a dressing
        # is still a salad. Standalone dressing names are components.
        for pattern, kind, form in [
            (r"\bsandwich(?:es)?\s*$", "dish", "sandwich"),
            (r"\b(?:dressing|vinaigrette)\s*$", "component", "dressing"),
            (r"\bsauce\s*$", "component", "sauce"),
            (r"\btopping\s*$", "component", "topping"),
            (r"\b(?:bread\s*)?crumbs\s*$", "component", "crumbs"),
            (r"\bbutter\s*$", "component", "butter"),
            (r"\b(?:dough|starter)\s*$", "component", "dough"),
            (r"\b(?:stock|broth)\s*$", "component", "stock"),
            (r"\b(?:spice mix|seasoning|rub)\s*$", "component", "seasoning"),
            (r"\b(?:salad|slaw)\b", "dish", "salad"),
            (r"\bstew\b", "dish", "stew"),
            (r"\bsoup\b", "dish", "soup"),
            (r"\bcake\b", "dish", "cake"),
            (r"\btart\b", "dish", "tart"),
            (r"\b(?:cookie|cookies|biscuit|biscuits)\s*$", "dish", "cookie"),
            (r"\b(?:pasta|spaghetti|penne|rigatoni)\b", "dish", "pasta"),
            (r"\b(?:curry|butter chicken)\s*$", "dish", "curry"),
            (r"\broast(?:ed)?\s+(?:chicken|beef|pork|lamb|turkey)\b", "dish", "roast"),
            (r"\b(?:bread|loaf)\s*$", "dish", "bread"),
        ]:
            match = re.search(pattern, head)
            if match:
                # A compound title ending in a sauce might name a whole dish.
                if kind == "component" and re.search(r"\band\b|&", head):
                    match = None
                    break
                result.update(preparation_kind=kind, dish_form=form)
                result["evidence"].append(dict(source_field="title", text=title, rule=pattern))
                break
    if not match:
        result["review_status"] = "unresolved"
    for field in ("headnote", "yield"):
        value = recipe.get(field) or ""
        for role, pattern in [("side", r"\b(?:as a side(?: dish)?|side dish|as a contrasting side)\b"),
                              ("main", r"\b(?:as a main(?: course)?|main course|meal in (?:its|their) own right)\b")]:
            match = re.search(pattern, value, re.I)
            if match and not re.search(r"\b(?:not|never)\b", value[max(0, match.start()-25):match.start()], re.I):
                # A mention may describe an adaptation or another dish. Return
                # source cues, never an asserted main/side role or hard filter.
                result["meal_role_cues"].append(role)
                result["evidence"].append(dict(source_field=field, text=match.group(), char_start=match.start(), char_end=match.end(), rule=pattern))
    result["meal_role_cues"] = sorted(set(result["meal_role_cues"]))
    return result


def build(recipes):
    return dict(version=VERSION, labels={rid: dict(source_hash=fingerprint(r), **classify(r)) for rid, r in recipes.items()})


class RecipeLabels:
    def __init__(self, recipes, path=None):
        bundle = json.loads(Path(path).read_text()) if path and Path(path).exists() else {}
        self.labels = {}
        self.stale = 0
        self.metadata = {k:v for k,v in bundle.items() if k != 'labels'}
        if bundle.get("version") in {VERSION, SEMANTIC_VERSION}:
            for rid, row in bundle.get("labels", {}).items():
                if rid in recipes and row.get("source_hash") == fingerprint(recipes[rid]):
                    if row.get("preparation_kind") not in KINDS or row.get("dish_form") not in FORMS:
                        raise ValueError("Invalid recipe discovery label")
                    self.labels[rid] = row
                else:
                    self.stale += 1
        self.total = len(recipes)
        self.version = hashlib.sha256(json.dumps(self.labels, sort_keys=True).encode()).hexdigest() if self.labels else None

    def get(self, rid, full=False):
        row = self.labels.get(rid, unknown())
        # The full evidence ledger stays in the sidecar; cards carry concise cues.
        return copy.deepcopy({k: v for k, v in row.items() if k not in ("source_hash", "evidence")}) | {
            **({'classifier':copy.deepcopy(self.metadata['classifier'])} if rid in self.labels and self.metadata.get('classifier') else {}),
            "evidence": [copy.deepcopy(e) if full else {k: v for k, v in e.items() if k != "text"} for e in row.get("evidence", [])]}

    def matches(self, rid, preparation_kind=None, dish_form=None):
        row = self.labels.get(rid, unknown())
        return (not preparation_kind or row["preparation_kind"] == preparation_kind) and (not dish_form or row["dish_form"] == dish_form)

    def unknown_count(self, ids, preparation_kind=None, dish_form=None):
        fields = [field for field, value in (("preparation_kind", preparation_kind), ("dish_form", dish_form)) if value]
        fields = fields or ["preparation_kind"]
        return sum(any(self.labels.get(rid, {}).get(field, "unknown") == "unknown" for field in fields) for rid in ids)

    def status(self):
        return dict(version=self.version, total=self.total, assessed=len(self.labels), stale=self.stale,
                    taxonomy_version=self.metadata.get('version'), classifier=self.metadata.get('classifier'),
                    positive_kind=sum(r['preparation_kind']!='unknown' for r in self.labels.values()),
                    positive_form=sum(r['dish_form']!='unknown' for r in self.labels.values()),
                    preparation_kinds=dict(Counter(row["preparation_kind"] for row in self.labels.values())),
                    unassessed=self.total-len(self.labels), provisional=True)
