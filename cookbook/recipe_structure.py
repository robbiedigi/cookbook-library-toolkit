"""Source-bound ingredient/method projections, separate from retained cookbook text.

Absence of a review is not a completeness claim. Reviewed prose extraction is an
editorial normalization, with exact evidence, not newly authored recipe content.
"""
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

from .recipe_labels import fingerprint

VERSION = "recipe-structure-1"
ROLES = {"required", "optional", "alternative", "serving", "processing", "as_printed"}
RELATIONSHIPS = {"variation_of", "component", "method_reference", "related_source"}


def display_text(entry):
    # Keep alternative formulas and serving scales visible even in compact
    # string projections. The detailed entry retains the separate group field.
    return (entry['group'] + ': ' if entry.get('group') else '') + entry['text']


def validate_evidence(rid, row, evidence, recipes):
    if 'fragment_id' in evidence:
        fragment = row.get('source_fragments', {}).get(evidence['fragment_id'])
        quote = evidence.get('quote')
        if not fragment or not isinstance(quote, str) or not quote or quote not in fragment['text']:
            raise ValueError('Fragment evidence is not an exact source excerpt')
        if 'recipe_id' in evidence or 'path' in evidence:
            raise ValueError('Ambiguous fragment evidence locator')
        return
    source_id = evidence.get("recipe_id", rid)
    if source_id != rid and source_id not in row.get("dependencies", {}):
        raise ValueError("Unbound structure dependency")
    value = recipes[source_id]
    path = evidence.get("path")
    if not isinstance(path, list) or not path:
        raise ValueError("Invalid source path")
    try:
        for key in path:
            if not isinstance(key, (str, int)) or isinstance(key, bool) or (isinstance(key, int) and key < 0):
                raise ValueError("Invalid source key")
            value = value[key]
    except (KeyError, IndexError, TypeError):
        raise ValueError("Invalid source path") from None
    quote = evidence.get("quote")
    if not isinstance(value, str) or not isinstance(quote, str) or not quote or quote not in value:
        raise ValueError("Structure evidence is not an exact source excerpt")


def validate_row(rid, row, recipes):
    if row.get("source_hash") != fingerprint(recipes[rid]):
        raise ValueError("Recipe structure source changed: " + rid)
    if row.get("status") not in {"reviewed", "source_incomplete"}:
        raise ValueError("Invalid recipe structure status")
    if not isinstance(row.get("limitations"), list) or any(not isinstance(x, str) for x in row["limitations"]):
        raise ValueError("Structure limitations must be explicit")
    for dep, value in row.get("dependencies", {}).items():
        if dep not in recipes or fingerprint(recipes[dep]) != value:
            raise ValueError("Recipe structure dependency changed: " + dep)
    # Some verified passages were missed entirely by an extractor. Retain them
    # as an explicit source addition, never silently rewrite the canonical row.
    fragments = row.get('source_fragments', {})
    if not isinstance(fragments, dict):
        raise ValueError('Source fragments must be an object')
    source_hashes = {recipes[i].get('source', {}).get('sha256')
                     for i in [rid, *row.get('dependencies', {})]} - {None}
    for fid, fragment in fragments.items():
        text = fragment.get('text')
        if not isinstance(fid, str) or not fid or not isinstance(text, str) or not text.strip():
            raise ValueError('Invalid source fragment')
        if fragment.get('text_sha256') != hashlib.sha256(text.encode()).hexdigest():
            raise ValueError('Source fragment text changed')
        source = fragment.get('source', {})
        if source.get('sha256') not in source_hashes or not source.get('member'):
            raise ValueError('Source fragment must cite the original book of a bound recipe')
        if not isinstance(fragment.get('review_basis'), str) or not fragment['review_basis'].strip():
            raise ValueError('Source fragment requires a reading basis')
    for field in ("ingredients", "method", "headnote", "cooking_notes"):
        if field not in row:
            continue
        if not isinstance(row[field], list):
            raise ValueError("Structure section must be an array")
        for entry in row[field]:
            if not isinstance(entry.get("text"), str) or not entry["text"].strip():
                raise ValueError("Empty normalized entry")
            if field == "ingredients" and entry.get("role") not in ROLES:
                raise ValueError("Invalid ingredient role")
            if field == "cooking_notes" and (not isinstance(entry.get('applicability'), str) or not entry['applicability'].strip()):
                raise ValueError('Cooking notes require an applicability explanation')
            if 'group' in entry and (not isinstance(entry['group'], str) or not entry['group'].strip()):
                raise ValueError("Invalid ingredient group")
            if not isinstance(entry.get("evidence"), list) or not entry["evidence"]:
                raise ValueError("Normalized entries require source evidence")
            for evidence in entry["evidence"]:
                validate_evidence(rid, row, evidence, recipes)
            if field == 'cooking_notes' and ' '.join(entry['text'].split()) != ' '.join(
                    ' '.join(e['quote'] for e in entry['evidence']).split()):
                raise ValueError('Cooking note text must preserve its cited excerpts')
    if 'cooking_notes' in row:
        review = row.get('cooking_notes_review', {})
        if review.get('status') not in {'reviewed_bounded', 'reviewed_with_source_limitation'}:
            raise ValueError('Cooking notes require bounded review coverage')
        if not isinstance(review.get('coverage'), str) or not review['coverage'].strip():
            raise ValueError('Cooking note coverage must be explicit')
        for name in ('reviewed_scope', 'unreviewed_scope', 'limitations'):
            if not isinstance(review.get(name), list) or any(not isinstance(x, str) or not x.strip() for x in review[name]):
                raise ValueError('Invalid cooking note review scope')
        if not review['reviewed_scope']:
            raise ValueError('Cooking note review scope cannot be empty')
        for name in ('omitted_method_branch', 'component_fallback'):
            branch = review.get(name)
            if branch is not None and (not isinstance(branch, dict) or branch.get('tool') != 'get_recipe'
                    or branch.get('recipe_id') not in row.get('dependencies', {})
                    or not isinstance(branch.get('reason'), str) or not branch['reason'].strip()):
                raise ValueError('Cooking note fallback requires a bound dependency and reason')
    if not isinstance(row.get('parent_links', []), list):
        raise ValueError('Parent links must be an array')
    for link in row.get('parent_links', []):
        parent = link.get('recipe_id')
        if parent == rid or parent not in row.get('dependencies', {}):
            raise ValueError('Parent link requires a distinct source-bound dependency')
        if link.get('relationship') not in RELATIONSHIPS:
            raise ValueError('Unknown recipe relationship')
        if not isinstance(link.get('application'), str) or not link['application'].strip():
            raise ValueError('Parent link requires an application explanation')
        if not isinstance(link.get('evidence'), list) or not link['evidence']:
            raise ValueError('Parent link requires source evidence')
        for evidence in link['evidence']:
            validate_evidence(rid, row, evidence, recipes)
    if "ingredients" not in row:
        raise ValueError("Reviewed structure requires an ingredient section")
    return copy.deepcopy(row)


class RecipeStructure:
    def __init__(self, recipes, path=None):
        self.recipes = recipes
        self.rows, self.stale = {}, []
        bundle = json.loads(Path(path).read_text()) if path and Path(path).exists() else {}
        if bundle and bundle.get("version") != VERSION:
            raise ValueError("Unknown recipe structure version")
        for rid, row in bundle.get("recipes", {}).items():
            # Stale source must never silently keep serving normalized claims.
            dependencies = row.get("dependencies", {})
            if rid not in recipes or row.get("source_hash") != fingerprint(recipes[rid]) or any(
                dep not in recipes or fingerprint(recipes[dep]) != h for dep, h in dependencies.items()
            ):
                self.stale.append(rid)
                continue
            self.rows[rid] = validate_row(rid, row, recipes)
        self.version = hashlib.sha256(json.dumps(self.rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def project(self, rid):
        raw, row = self.recipes[rid], self.rows.get(rid)
        result = dict(raw)
        if row:
            result["ingredients"] = [display_text(e) for e in row["ingredients"]]
            if "method" in row:
                result["instructions"] = [e["text"] for e in row["method"]]
            if "headnote" in row:
                result["headnote"] = "\n\n".join(e["text"] for e in row["headnote"])
        return result

    def summary(self, rid):
        row = self.rows.get(rid)
        raw = self.recipes[rid]
        return dict(version=VERSION, status=row["status"] if row else
                    "stale_review" if rid in self.stale else "source_sections_unreviewed",
                    normalized=bool(row),
                    parent_recipe_ids=list(dict.fromkeys(link['recipe_id'] for link in row.get('parent_links', []))) if row else [],
                    restored_source_fragment_count=len(row.get('source_fragments', {})) if row else 0,
                    requires_variant_review=bool(row and (row['status']=='source_incomplete' or
                        any(e['role'] in {'optional','alternative'} for e in row['ingredients']) or
                        len({e['group'] for e in row['ingredients'] if e.get('group')}) > 1)),
                    limitations=copy.deepcopy(row["limitations"] if row else
                        (["Ingredient section is missing; ingredient exclusions cannot establish absence."] if not raw.get("ingredients") else []) +
                        (["Method section is missing; consult the retained source references."] if not raw.get("instructions") else [])))

    def sections(self, rid):
        raw, row = self.recipes[rid], self.rows.get(rid)
        result = self.summary(rid)
        result["basis"] = "source-bound editorial normalization" if row else "existing source extraction; completeness not independently verified"
        result["evidence_path_basis"] = "Original recipe record. For normalized records, original_sections retains the original ingredients, instructions and headnote; blocks and supporting notes remain unchanged. recipe_id on evidence identifies a separately cited source record. fragment_id identifies a separately retained, verified source passage in source_fragments."
        for name, field in (("ingredients", "ingredients"), ("method", "instructions")):
            entries = copy.deepcopy(row[name]) if row and name in row else [
                dict(text=text, evidence=[dict(path=[field, i], quote=text)])
                for i, text in enumerate(raw.get(field, []))]
            result[name] = dict(lines=[display_text(e) for e in entries], entries=entries)
        if row:
            result["source_hash"] = row["source_hash"]
            result["dependencies"] = copy.deepcopy(row.get("dependencies", {}))
            if row.get('source_fragments'):
                result['source_fragments'] = copy.deepcopy(row['source_fragments'])
                for fragment in result['source_fragments'].values():
                    if fragment['source'].get('file'):
                        fragment['source']['file'] = Path(fragment['source']['file']).name
            result['parent_links'] = []
            for link in row.get('parent_links', []):
                parent = self.recipes[link['recipe_id']]
                source = copy.deepcopy(parent['source'])
                source['file'] = Path(source['file']).name
                result['parent_links'].append(dict(copy.deepcopy(link), title=parent['title'],
                    book=parent['book'], author=parent['author'], source=source,
                    full_read=dict(tool='get_recipe', recipe_id=link['recipe_id'])))
            if "headnote" in row:
                result["headnote"] = dict(entries=copy.deepcopy(row["headnote"]))
            if 'cooking_notes' in row:
                result['cooking_notes'] = copy.deepcopy(row['cooking_notes'])
                for note in result['cooking_notes']:
                    note['sources'] = []
                    for evidence in note['evidence']:
                        if 'fragment_id' in evidence:
                            source = copy.deepcopy(row['source_fragments'][evidence['fragment_id']]['source'])
                            citation = dict(fragment_id=evidence['fragment_id'], source=source)
                        else:
                            source_id = evidence.get('recipe_id', rid)
                            cited = self.recipes[source_id]
                            source = copy.deepcopy(cited['source'])
                            citation = dict(recipe_id=source_id, title=cited['title'], book=cited['book'], source=source)
                            # A note can occur later than the recipe's first page.
                            # Keep the recipe citation and the exact block locator distinct.
                            node = cited
                            for key in evidence['path'][:-1]:
                                node = node[key]
                            if isinstance(node, dict):
                                locator = {k: copy.deepcopy(node[k]) for k in
                                    ('path', 'pdf_page', 'pdf_pages', 'printed_page', 'pages', 'anchors') if node.get(k)}
                                if locator:
                                    citation['excerpt_location'] = locator
                        if source.get('file'):
                            source['file'] = Path(source['file']).name
                        if citation not in note['sources']:
                            note['sources'].append(citation)
                result['parent_note_coverage'] = copy.deepcopy(row['cooking_notes_review'])
        return result

    def status(self):
        return dict(version=self.version, schema_version=VERSION, total=len(self.recipes),
                    reviewed=len(self.rows), stale=len(self.stale),
                    unreviewed=len(self.recipes)-len(self.rows),
                    statuses=dict(Counter(row["status"] for row in self.rows.values())),
                    missing_ingredients=sum(not self.project(rid).get("ingredients") for rid in self.recipes),
                    missing_method=sum(not self.project(rid).get("instructions") for rid in self.recipes),
                    completeness_verified=False)
