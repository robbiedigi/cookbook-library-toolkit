"""Local BM25 + two dense-vector searches, with optional local LLM reasoning."""
from __future__ import annotations
import difflib
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from collections import defaultdict
from contextlib import closing
from functools import lru_cache
from pathlib import Path

import numpy as np

from .enrich import fingerprint, load_profiles
from .importer import load_recipes
from .ingredient_relationships import (CHILDREN, SEAFOOD_ADDITIONS, NON_ANIMAL_COMPOUNDS, NON_INGREDIENT_HOMONYMS, MUSHROOM_EXAMPLE_PATTERN, PORK_ALTERNATIVES, ANIMAL_TERMS, BEEF_TERMS, PORK_TERMS,
    VERSION as INGREDIENT_VERSION, derivative_terms, related_terms)

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
MODEL_VERSION = "hybrid-1"
STOP = set("a an the and or with of for to in on by from something nice find me recipe recipes please want i my some that this is it have like can make would which".split())
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


def normalize(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def words(text):
    return re.findall(r"[a-z0-9]+", normalize(text))


@lru_cache(maxsize=8192)
def ingredient_text(text):
    return re.sub(r"\s+", " ", re.sub(r"[-‐‑–—]", " ", normalize(text))).strip()


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


def ingredient_vocabulary():
    return dict(version=INGREDIENT_VERSION, aliases=dict(INGREDIENT_ALIASES), children={k:list(v) for k,v in CHILDREN.items()},
                groups={k: list(v) for k, v in INGREDIENT_GROUPS.items()},
                excluded_derivatives={k: list(v) for k, v in INGREDIENT_DERIVATIVES.items()},
                excluded_compounds=list(NON_ANIMAL_COMPOUNDS),
                excluded_homonyms={k:list(v) for k,v in NON_INGREDIENT_HOMONYMS.items()},
                limitations=["Groups are bounded discovery expansions, not complete food taxonomies or allergy guarantees.",
                             "A child food matches its parent, but parents do not imply a specific cut. Unqualified short ribs, mince and fillet remain ambiguous.",
                             "Literal animal foods omit named stocks/fats; group:beef/chicken/pork/lamb/fish includes known derivatives for broader exclusion.",
                             "Matches identify ingredient mentions, not dominance, mandatory use or variant coexistence.",
                             "Explicit mushroom example lists can elide the word mushroom after oyster; known lists are excluded from animal matching, without suppressing separate seafood mentions.",
                             "Coriander leaves match cilantro; unqualified coriander, coriander seeds and roots are not silently equated with leaves. Spring onions and scallions remain distinct.",
                             "Unknown compound composition is not inferred: for example Worcestershire sauce is not automatically classified as seafood."])


def ingredient_mentions(text):
    """Keep the longest named ingredient instead of splitting compound names."""
    text = ingredient_text(text)
    found = []
    for term in INGREDIENT_TERMS:
        pattern = ingredient_pattern(term)
        if re.search(pattern, text):
            found.append(INGREDIENT_ALIASES.get(term, term))
            text = re.sub(pattern, " ", text)
    return sorted(set(found))


def fallback_profile(r):
    """Transparent local hints. These are not model-generated culinary judgments."""
    title, source = normalize(r["title"]), normalize(r["headnote"])
    member = r["source"]["member"]
    source_course = next((c.removeprefix("ry-") for b in r["blocks"] for c in b["classes"] if c.startswith("ry-")), None)
    king_course = {"sweets": "dessert", "salad": "salad", "vegetables": "side", "meat": "main", "seafood": "main", "noodles": "main", "bread": "bread", "snacks": "snack", "sauces": "condiment"}
    if source_course in king_course:
        course = king_course[source_course]
    elif r["book_id"] == "braising" and any(member.endswith(x) for x in ["part0007.html", "part0008.html", "part0009.html", "part0010.html", "part0011.html", "part0012.html"]):
        course = "main"
    elif re.search(r"\b(cake|cookies|tart|crisp|toffee|shortbread|parfait|ice cream|sorbet|custard|pudding)\b", title):
        course = "dessert" if not re.search(r"\b(onion|cheese|chard|leek|bacon|savory|potato|crab|fish)\b", title) else "main"
    elif "Orange Cream Ice" == r["title"] or re.search(r"\b(braised|poached|baked) (pears|apples|fruit)\b", title): course = "dessert"
    elif "salad" in title or "slaw" in title: course = "salad"
    elif "soup" in title: course = "soup"
    elif re.search(r"\b(fettuccine|penne|gnocchi|sandwich|ragout|chili|tart|frittata|meatballs|pilaf|risotto|kebab|fish|scallop|halibut|cod)\b", title): course = "main"
    elif re.search(r"\b(broth|stock|sauce|oil|mayonnaise|dressing|butter|pickles|croutons|crumbs|dough)\b", title) and " with " not in title: course = "condiment"
    elif any(ingredient_matches(x, [r["title"]]) for x in ["chicken", "pork", "beef", "lamb", "salmon", "shrimp", "duck", "pasta", "noodle"]): course = "main"
    else: course = "side"
    # Retain the author's own descriptive language for vibe search.
    return {"id": r["id"], "course": course, "description": (r["headnote"] or r.get("source_prose_excerpt", ""))[:1000],
            "flavors": [], "textures": [], "techniques": [], "cuisine_associations": [],
            "pairing_role": "", "richness": "unknown", "generated": False, "model": "source-headnote + local course rules"}


def recipe_profile(r, profiles):
    p = profiles.get(r["id"], {})
    return p if p.get("source_hash") == fingerprint(r) else fallback_profile(r)


def embedding_texts(r, p):
    content = f"{r['title']}\nIngredients: " + "; ".join(r["ingredients"]) + "\n" + r["headnote"][:450]
    if r.get('source_prose_excerpt'):
        content += '\nSource prose (unparsed): ' + r['source_prose_excerpt'][:1800]
        content += '\nSource title aliases: ' + '; '.join(r.get('aliases', []))
    profile = f"{r['title']}. {p['course']}. {p['description']} " + "; ".join(p.get("flavors", []) + p.get("textures", []) + p.get("techniques", []) + p.get("cuisine_associations", [])) + ". " + p.get("pairing_role", "")
    return content, profile


def embedder(data_dir):
    os.environ.setdefault("HF_HOME", str((data_dir.parent / ".cache/huggingface").resolve()))
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import onnxruntime
    onnxruntime.disable_telemetry_events()
    from fastembed import TextEmbedding
    return TextEmbedding(EMBED_MODEL, cache_dir=str(data_dir.parent / ".cache/fastembed"), threads=2, local_files_only=True)


def build_index(data_dir, reuse_from=None):
    recipes, profiles = load_recipes(data_dir), load_profiles(data_dir)
    model = embedder(data_dir)
    texts, ids, kinds = [], [], []
    for r in recipes:
        p = recipe_profile(r, profiles)
        for kind, text in zip(["content", "profile"], embedding_texts(r, p)):
            ids.append(r["id"]); kinds.append(kind); texts.append(text)
    cached = {}
    if reuse_from is not None:
        reuse_from = Path(reuse_from)
        prior_report = json.loads((reuse_from / 'index_report.json').read_text())
        if (prior_report['source_fingerprint'] != hashlib.sha256((reuse_from / 'recipes.jsonl').read_bytes()).hexdigest()
                or prior_report['embedding_model'] != EMBED_MODEL or prior_report['version'] != MODEL_VERSION):
            raise ValueError('Recipe vector cache source/model mismatch')
        prior_recipes, prior_profiles = load_recipes(reuse_from), load_profiles(reuse_from)
        inputs = {(r['id'], k): text for r in prior_recipes
                  for k, text in zip(('content', 'profile'), embedding_texts(r, recipe_profile(r, prior_profiles)))}
        # The saved SQL profile is part of the actual embedding input. A later
        # profile/rule change must not make an old vector look reusable.
        with closing(sqlite3.connect(reuse_from / 'search.sqlite')) as db, db:
            saved_inputs = {(rid, k): text for rid, record, profile in db.execute('SELECT id,record,profile FROM recipes')
                            for k, text in zip(('content', 'profile'), embedding_texts(json.loads(record), json.loads(profile)))}
        if inputs != saved_inputs: raise ValueError('Recipe vector cache profile mismatch')
        with np.load(reuse_from / 'vectors.npz', allow_pickle=False) as prior:
            keys = list(zip(prior['ids'].tolist(), prior['kinds'].tolist()))
            if len(keys) != len(set(keys)) or set(keys) != set(inputs):
                raise ValueError('Recipe vector cache identifiers mismatch')
            cached = {(rid, kind, inputs[(rid, kind)]): v.copy() for (rid, kind), v in zip(keys, prior['vectors'])}
    keys = list(zip(ids, kinds, texts))
    missing = [i for i, key in enumerate(keys) if key not in cached]
    new = np.array(list(model.passage_embed([texts[i] for i in missing], batch_size=24)), dtype=np.float32) if missing else None
    if new is not None:
        new /= np.maximum(np.linalg.norm(new, axis=1, keepdims=True), 1e-12)
    produced = dict(zip(missing, new)) if new is not None else {}
    vectors = np.array([cached[key] if key in cached else produced[i] for i, key in enumerate(keys)], dtype=np.float32)
    np.savez_compressed(data_dir / "vectors.npz", vectors=vectors, ids=np.array(ids), kinds=np.array(kinds))
    dbpath = data_dir / "search.sqlite"
    with closing(sqlite3.connect(dbpath)) as db, db:
        db.executescript("DROP TABLE IF EXISTS recipes; DROP TABLE IF EXISTS recipe_fts; CREATE TABLE recipes(id TEXT PRIMARY KEY, record TEXT NOT NULL, profile TEXT NOT NULL); CREATE VIRTUAL TABLE recipe_fts USING fts5(id UNINDEXED, title, ingredients, description, tokenize='unicode61 remove_diacritics 2');")
        for r in recipes:
            p = recipe_profile(r, profiles)
            db.execute("INSERT INTO recipes VALUES(?,?,?)", (r["id"], json.dumps(r, ensure_ascii=False), json.dumps(p, ensure_ascii=False)))
            db.execute("INSERT INTO recipe_fts VALUES(?,?,?,?)", (r["id"], r["title"], "\n".join(r["ingredients"]), r["headnote"] + "\n" + p["description"]))
    report = {"recipes": len(recipes), "vectors": len(vectors), "dimensions": int(vectors.shape[1]), "embedding_model": EMBED_MODEL,
              "version": MODEL_VERSION, "generated_profiles": sum(recipe_profile(r, profiles).get("generated", False) for r in recipes),
              "source_fingerprint": hashlib.sha256((data_dir / "recipes.jsonl").read_bytes()).hexdigest()}
    if reuse_from is not None:
        report.update(reused_vectors=len(keys)-len(missing), embedded_vectors=len(missing))
    (data_dir / "index_report.json").write_text(json.dumps(report, indent=2))
    return report


def local_plan(query, include=None, exclude=None, course=None):
    q = normalize(query)
    pairing = bool(re.search(r"\b(follow|after|pair|alongside|accompany|goes with|go with|serve with)\b", q))
    plan = {"mode": "pairing" if pairing else "search", "queries": [query], "include": list(include or []), "exclude": list(exclude or []), "course": course, "interpretation": "Searching recipe titles, ingredients, and descriptions.", "planner": "local rules"}
    if not course:
        if "dessert" in q: plan["course"] = "dessert"
        elif "salad" in q: plan["course"] = "salad"
        elif "soup" in q: plan["course"] = "soup"
        elif re.search(r"\bside\b", q): plan["course"] = "side"
    # Never turn ingredients in a preceding meal into requirements for its dessert.
    if pairing and plan["course"] == "dessert":
        plan["queries"] = ["refreshing citrus fruit dessert with bright tart flavors", "poached baked fruit dessert with restrained richness", "rustic fruit cake dessert"]
        plan["interpretation"] = "Looking for desserts offering a fresh contrast or a rustic finish. These are local search directions; the meal is not yet assessed by an LLM."
    elif not pairing:
        negative = r"\b(?:without|exclude|excluding|no)\s+(.+?)(?=[.;]|\b(?:but|with|using|contains)\b|$)"
        for clause in re.finditer(negative, q):
            plan["exclude"].extend(ingredient_mentions(clause[1]))
        positive = re.sub(negative, " ", q).strip()
        discovery = bool(re.search(r"\b(options|ideas|twists?|inspiration|alternatives|perhaps|maybe|or)\b", positive))
        # Only explicit ingredient clauses or concise requests ("a kale salad")
        # imply requirements. A descriptive list of ideas remains soft relevance.
        if not discovery:
            bare = canonical_ingredient(positive)
            if bare in INGREDIENT_TERMS or bare in INGREDIENT_ALIASES.values():
                plan["include"].append(bare)
            for clause in re.finditer(r"\b(?:with|using|contains)\s+([^.;]+)", positive):
                plan["include"].extend(ingredient_mentions(clause[1]))
            simple = re.fullmatch(r"(?:a |an |some )?(.+?)\s+(?:salad|soup)", positive)
            if simple:
                term = canonical_ingredient(simple[1])
                if term in INGREDIENT_TERMS or term in INGREDIENT_ALIASES.values():
                    plan["include"].append(term)
        if not plan["include"]:
            plan["interpretation"] = "Query ingredients describe preferences; use include/exclude for required ingredients."
    plan["include"], plan["exclude"] = sorted(set(plan["include"])), sorted(set(plan["exclude"]))
    return plan


def llm_plan(query, base):
    from .llm import ask_json
    schema = {"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "string"}}, "include": {"type": "array", "items": {"type": "string"}}, "exclude": {"type": "array", "items": {"type": "string"}}, "course": {"type": ["string", "null"]}, "interpretation": {"type": "string"}, "mode": {"type": "string", "enum": ["search", "pairing"]}}, "required": ["queries", "include", "exclude", "course", "interpretation", "mode"]}
    p, _ = ask_json("Plan searches of a personal recipe library. Separate explicit constraints from preferences. For pairing, infer complementary culinary directions and search for the requested course, not ingredients in the preceding meal. Return 1-3 short search queries describing the TARGET recipe's flavors, texture and ingredients. Never repeat the preceding meal in those queries. Example: dessert after a rich roast -> 'bright citrus fruit dessert', 'poached pear dessert', 'simple fruit cake'. Explain the relationship to the meal only in interpretation. Do not invent hard ingredient constraints. Light means perceived richness unless otherwise specified. Valid course values: main,salad,side,dessert,soup,snack,bread,condiment,other or null.", {"request": query, "explicit_filters": base}, schema, 1200)
    if not isinstance(p.get("queries"), list) or not p["queries"] or not all(isinstance(x, str) for x in p["queries"]):
        raise ValueError("Local model returned an invalid search plan.")
    for field in ("include", "exclude"):
        if not isinstance(p.get(field), list) or not all(isinstance(x, str) for x in p[field]):
            raise ValueError("Local model returned invalid ingredient filters.")
    if p.get("course") not in [None, "main", "salad", "side", "dessert", "soup", "snack", "bread", "condiment", "other"]:
        raise ValueError("Local model returned an invalid course.")
    p["planner"] = "local LLM"
    return p


class SearchEngine:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        with closing(sqlite3.connect(data_dir / "search.sqlite")) as db, db:
            rows = db.execute("SELECT id,record,profile FROM recipes").fetchall()
        self.recipes = {rid: json.loads(r) for rid, r, p in rows}
        self.profiles = {rid: json.loads(p) for rid, r, p in rows}
        with np.load(data_dir / "vectors.npz", allow_pickle=False) as v:
            self.vectors, self.ids, self.kinds = v["vectors"], v["ids"], v["kinds"]
        self.model = embedder(data_dir)
        from .recipe_labels import RecipeLabels
        self.recipe_labels = RecipeLabels(self.recipes, data_dir / "recipe_discovery_labels.json")
        from .recipe_structure import RecipeStructure
        self.raw_recipes = self.recipes
        self.structure = RecipeStructure(self.raw_recipes, data_dir / "recipe_structure.json")
        self.recipes = {rid: self.structure.project(rid) for rid in self.raw_recipes}
        self.keyword_db = None
        if self.structure.rows:
            # Rebuild the keyword view in memory; retained indexes and label
            # fingerprints continue to describe the unchanged original corpus.
            self.keyword_db = sqlite3.connect(":memory:", check_same_thread=False)
            self.keyword_db.execute("CREATE VIRTUAL TABLE recipe_fts USING fts5(id UNINDEXED, title, ingredients, description, tokenize='unicode61 remove_diacritics 2')")
            self.keyword_db.executemany("INSERT INTO recipe_fts VALUES(?,?,?,?)", [
                (rid, r["title"], "\n".join(r["ingredients"]), r["headnote"] + "\n" + self.profiles[rid]["description"])
                for rid, r in self.recipes.items()])

    def search(self, query, limit=6, include=None, exclude=None, course=None, book=None, reason=False, offset=0,
               prefer=None, preparation_kind=None, dish_form=None):
        if not query.strip(): return {"query": query, "results": [], "plan": {}, "eligible_count": 0}
        plan = local_plan(query, include, exclude, course)
        exact_title = any(normalize(query).strip() in
                          [normalize(t).strip() for t in [r['title'], *r.get('aliases', [])]]
                          for r in self.recipes.values() if not book or r['book_id'] == book)
        if exact_title:
            # A named variation may express its ingredients in prose. Looking up
            # its exact title must not turn title words into ingredient filters.
            # Explicit caller filters still apply before every retrieval path.
            plan.update(mode='exact_title', queries=[query], include=list(include or []),
                        exclude=list(exclude or []), course=course,
                        interpretation='Exact source title or alias; explicit filters retained.')
        if reason and not exact_title:
            plan = llm_plan(query, {"include": include or [], "exclude": exclude or [], "course": course})
            plan["include"] = sorted(set(plan["include"] + (include or [])))
            plan["exclude"] = sorted(set(plan["exclude"] + (exclude or [])))
            if course: plan["course"] = course
        prefer = sorted({canonical_ingredient(t) for t in (prefer or [])})
        # Explicit soft preferences override ingredient requirements inferred from
        # prose, but never override a caller's explicit include constraint.
        explicit_include = {canonical_ingredient(t) for t in (include or [])}
        plan["include"] = [t for t in plan["include"] if canonical_ingredient(t) not in prefer or canonical_ingredient(t) in explicit_include]
        if (preparation_kind or dish_form) and course is None and plan["course"]:
            plan["course"] = None
            plan["interpretation"] += " Explicit discovery labels replace the automatically inferred coarse course; no course filter was supplied."
        labels = getattr(self, "recipe_labels", None)
        plan.update(prefer=prefer, preparation_kind=preparation_kind, dish_form=dish_form,
                    ingredient_expansions={t: list(INGREDIENT_GROUPS[t]) for t in
                        sorted(set(plan["include"] + plan["exclude"] + prefer)) if t in INGREDIENT_GROUPS})
        eligible = {rid for rid, r in self.recipes.items()
                    if (not book or r["book_id"] == book)
                    and (not plan["course"] or self.profiles[rid]["course"] == plan["course"])
                    and all(ingredient_matches(t, r["ingredients"]) for t in plan["include"])
                    and not any(ingredient_matches(t, r["ingredients"]) for t in plan["exclude"])}
        before_labels = len(eligible)
        unknown_before_labels = labels.unknown_count(eligible, preparation_kind, dish_form) if labels else before_labels
        if preparation_kind or dish_form:
            if labels is None:
                raise ValueError("Recipe discovery labels are unavailable; build them before using label filters.")
            eligible = {rid for rid in eligible if labels.matches(rid, preparation_kind, dish_form)}
        scores, signals = defaultdict(float), defaultdict(set)
        # Filtering happens BEFORE taking the top candidates in every retrieval path.
        def add(ranked, signal, weight=1):
            for rank, rid in enumerate(ranked, 1):
                scores[rid] += weight / (60 + rank)
                signals[rid].add(signal)
        queries = plan["queries"][:3] or [query]
        qs = np.array(list(self.model.query_embed(queries)), dtype=np.float32)
        qs /= np.maximum(np.linalg.norm(qs, axis=1, keepdims=True), 1e-12)
        for kind in ("content", "profile"):
            indices = [i for i, rid in enumerate(self.ids) if self.kinds[i] == kind and rid in eligible]
            if indices:
                sims = (self.vectors[indices] @ qs.T).max(axis=1)
                ranked = [str(self.ids[indices[i]]) for i in sorted(range(len(indices)), key=lambda i: (-float(sims[i]), str(self.ids[indices[i]])))]
                add(ranked, "semantic " + kind)
        query_terms = [t for t in words(" ".join(queries)) if t not in STOP]
        if query_terms:
            expression = " OR ".join('"' + t + '"' for t in sorted(set(query_terms)))
            sql = "SELECT id FROM recipe_fts WHERE recipe_fts MATCH ? ORDER BY bm25(recipe_fts,0,8,4,1), id"
            if getattr(self, "keyword_db", None) is not None:
                matches = self.keyword_db.execute(sql, (expression,)).fetchall()
            else:
                with closing(sqlite3.connect(self.data_dir / "search.sqlite")) as db, db:
                    matches = db.execute(sql, (expression,)).fetchall()
            add([rid for (rid,) in matches if rid in eligible], "keyword")
        nq = normalize(query).strip()
        title_scores = []
        for rid in eligible:
            r = self.recipes[rid]; nt = normalize(r["title"])
            ratio = difflib.SequenceMatcher(None, nq, nt).ratio()
            if nq == nt or nq in [normalize(t).strip() for t in r.get('aliases', [])]: scores[rid] += 10; signals[rid].add("exact title")
            elif len(nq) >= 6 and nq in nt: scores[rid] += .05; signals[rid].add("title phrase")
            if ratio > .55: title_scores.append((ratio, rid))
        add([rid for _, rid in sorted(title_scores, reverse=True)], "similar title", 1.3)
        preferred = {rid: [t for t in prefer if ingredient_matches(t, self.recipes[rid]["ingredients"])] for rid in scores} if prefer else {}
        for rid, matched in preferred.items():
            if matched:
                scores[rid] += .03 * len(matched) / len(prefer)
                signals[rid].add("preferred ingredients")
        candidates = sorted(scores, key=lambda rid: (-scores[rid], rid))
        candidate_count = len(candidates)
        ranking_fingerprint = hashlib.sha256(json.dumps(
            ["hybrid-paged-ingredients-2", [(rid, scores[rid]) for rid in candidates]], separators=(",", ":")
        ).encode()).hexdigest()
        reasons = {}
        if reason and candidates:
            candidates, reasons = self.rerank(query, plan, candidates, limit)
        results = []
        for rid in candidates[offset:offset + limit]:
            r, p = self.recipes[rid], self.profiles[rid]
            results.append({"id": rid, "title": r["title"], "book": r["book"], "author": r["author"], "page": r["source"]["printed_page"],
                            "course": p["course"], "description": p["description"], "generated_profile": p.get("generated", False),
                            "reason": reasons.get(rid, "Matched " + ", ".join(sorted(signals[rid])) + "."),
                            "signals": sorted(signals[rid]), "score": round(scores[rid], 5), "warnings": r["warnings"],
                            "preferred_ingredient_matches": preferred.get(rid, []),
                            **({"discovery_labels": labels.get(rid)} if labels else {})})
        return {"query": query, "plan": plan, "eligible_count": len(eligible), "candidate_count": candidate_count, "results": results,
                "ranking_fingerprint": ranking_fingerprint, "offset": offset,
                "has_more": not reason and offset + len(results) < candidate_count,
                "label_filter": {"preparation_kind": preparation_kind, "dish_form": dish_form,
                                 "eligible_before_labels": before_labels, "eligible_after_labels": len(eligible),
                                 "unknown_before_labels": unknown_before_labels,
                                 "coverage": labels.status() if labels else None},
                "limitations": [] if reason else ["Local search: culinary profiles may be inferred; pairing uses fixed search directions rather than an LLM assessment."]}

    def rerank(self, query, plan, candidates, limit):
        from .llm import ask_json
        schema = {"type": "object", "properties": {"choices": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["id", "reason"]}}}, "required": ["choices"]}
        data = [{"id": rid, "title": self.recipes[rid]["title"], "description": self.profiles[rid]["description"][:650], "ingredients": self.recipes[rid]["ingredients"], "preparation": " ".join(self.recipes[rid]["instructions"])[:1200], "warnings": self.recipes[rid]["warnings"]} for rid in candidates[:12]]
        answer, _ = ask_json(f"Select up to {limit} recipes from these candidates for the user's request. Return only provided IDs, no repeats. Each reason must be one sentence, at most 35 words. Ground explanations in ingredients and preparation. For pairings assess contrast/continuity with the meal, and offer diverse alternatives. Distinguish bright flavor from low richness: mascarpone, cream, butter and cake can be rich even with citrus or fruit. Mention relevant tradeoffs. Do not invent quantities or cooking times. If no recipe fits, return an empty choices list. Do not silently relax requirements. Interpret profiles as fallible inferences; source ingredients and instructions take precedence.", {"request": query, "plan": plan, "candidates": data}, schema, 1800)
        choices, reasons = [], {}
        for c in answer["choices"]:
            if c["id"] in candidates[:12] and c["id"] not in reasons:
                choices.append(c["id"]); reasons[c["id"]] = c["reason"]
        return choices, reasons

    def recipe(self, rid):
        return {**self.recipes[rid], "profile": self.profiles[rid]}
