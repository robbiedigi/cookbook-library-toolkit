"""Deterministic migration of accepted recipe text and dependency reviews.

This is an offline converter, not a recipe parser. It reuses the frozen
source-bound structure validator and cooking projection without loading v1
search, model, database or service modules. The archive remains the authority
for original/corrected text and review rationales.
"""
import argparse
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .legacy import (JsonlMapping, at_path, citation, digest, edition_id, encoded,
                     load_legacy, source_span)

VERSION = "library-v2-draft-1"
DEFAULT_BASELINE = Path(__file__).resolve().parents[1] / "data" / "baseline-batch02"
CORRECTIONS = ("normalizations", "transcription_corrections", "content_edits",
               "inferences", "author_errata", "interpretations", "owner_decisions", "source_cleanups")
MAPPED = {"id", "book_id", "book", "author", "title", "aliases", "ingredients",
          "instructions", "headnote", "yield", "blocks", "warnings", "source",
          "supporting_notes", "pdf_pages", "printed_pages", "source_prose_excerpt",
          "yield_qualification", "extraction_status", "boundary_review", "source_qualifications"}
ARCHIVED = set(CORRECTIONS) | {"source_text", "references", "record_kind", "review",
    "review_id", "review_key", "book_index_links", "original_blocks", "original_warnings",
    "recovered_source_lines", "parent_id", "component_ids", "variant_ids",
    "source_region_reviews", "excluded_reading_artifacts", "title_basis",
    "supplemental_sources", "supplemental_source_text", "profile", "course",
    "original_ingredients", "source_review_history"}

QUANTITY_START = r"(?:\d|[¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]|(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b)"


def _heading_kind(text):
    text = " ".join(text.strip().split())
    if re.fullmatch(r"optional\s*:?", text, re.I):
        return "ambiguous_optional"
    names = r"(?:garnish(?:es)?|garnishings?|toppings?|accompaniments?|fillings?|sauces?|decorations?|ingredients?|serving)"
    if re.match(r"^(?:for\s+)?(?:the\s+)?optional\s+" + names + r"\s*(?::|$)", text, re.I) or re.match(
            r"^(?:for\s+)?(?:the\s+)?" + names + r"\s*(?:\(\s*optional\s*\)|optional)\s*(?::|$)", text, re.I):
        return "optional"
    if re.search(r"\boptional\b", text, re.I) and re.match(r"^(?:optional\b|for\b|to\b)", text, re.I):
        return "ambiguous_optional"
    if re.search(r"\boptional\b", text, re.I) and (re.match(r"^" + names + r"\b", text, re.I) or
            (text.startswith("(") and re.search(r"\b" + names + r"\b", text, re.I))):
        return "ambiguous_optional"
    if re.match(r"^(?:if\b|either\b|alternatively\b|or\s*:)", text, re.I):
        return "ambiguous_optional"
    if re.fullmatch(r"(?:for\s+(?:the\s+)?[^:]+|to\s+serve)\s*:?", text, re.I):
        return "boundary"
    return None


def _qualifier_start(text):
    text = text.lstrip()
    return bool(re.match(r"^(?:[\[(]\s*)?(?:or\b|if\b|optional\b|alternatively\b|either\b)", text, re.I) or
                (text.startswith(("(", "[")) and re.search(r"\b(?:optional|if|or)\b", text, re.I)))


def _food_alternative(text):
    # An amount range changes quantity, not ingredient identity. The other
    # exemption describes seasoning amount rather than a replacement food.
    text = re.sub(r"^\s*(?:\d+(?:[¼½¾⅓⅔⅛⅜⅝⅞])?|[¼½¾⅓⅔⅛⅜⅝⅞])\s+or\s+(?:\d+(?:[¼½¾⅓⅔⅛⅜⅝⅞])?|[¼½¾⅓⅔⅛⅜⅝⅞])\b", "AMOUNT_RANGE", text, flags=re.I)
    text = re.sub(r",?\s*or\s+to\s+taste\b", "", text, flags=re.I)
    return bool(re.search(r"\b(?:or|such as|for example|either|alternatively)\b|and/or|\be\.g\.", text, re.I))


def ingredient_presence(text, reviewed_role=None, group=None):
    """Bounded presence evidence; a heading or extraction role is not a variant.

    Explicit alternatives remain uncertain even when a quantity is printed.
    Quantity syntax is used only to identify a listed ingredient occurrence,
    never to parse or rewrite the amount, certify completeness or infer absence.
    """
    alternative = _food_alternative(text)
    optional = bool(re.search(r"\boptional\b|\bif (?:desired|using|you like|needed)\b", text, re.I))
    serving = reviewed_role == "serving" or bool(re.search(r"\b(?:to serve|for serving|to garnish)\b", text, re.I))
    if reviewed_role == "alternative" or alternative:
        return None, group or "alternative", "explicit_alternative"
    if reviewed_role == "optional" or optional:
        return True, None, "explicit_optional"
    if re.search(r"\bor\s+to\s+taste\b", text, re.I):
        return None, "quantity_qualification", "source_quantity_qualification"
    if re.search(r"\b(?:if|unless)\b", text, re.I):
        return None, "conditional", "source_conditional_scope"
    if serving:
        return None, "serving", "serving_scope"
    if reviewed_role in {"required", "processing"}:
        return False, None, "reviewed_" + reviewed_role
    quantified = bool(re.match(r"^\s*" + QUANTITY_START, text, re.I))
    if quantified:
        return False, None, "source_quantity_line"
    return None, None, "unknown"


def ingredient_occurrences(text, reviewed_role=None, group=None, *, exception=None, inherited_scope=None):
    """Administrative source ranges; never split or rewrite cooking text.

    Physical wraps join only through visible syntax, measurement-only lines,
    or a bounded set of incomplete quantity modifiers. Optional/alternative
    language is assessed per resulting occurrence, never for the whole list.
    """
    lines = []
    for match in re.finditer(r"[^\r\n]+", text):
        start, end = match.span()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            lines.append([start, end])
    if group and lines and text.startswith(group + ": "):
        lines[0][0] += len(group) + 2
    units_only = re.compile(r"^\s*(?:" + QUANTITY_START + r")[\d\s/.,¼½¾⅓⅔⅛⅜⅝⅞–—-]*(?:(?:cups?|tablespoons?|teaspoons?|tbsps?|tsps?|quarts?|pints?|liters?|litres?|ounces?|pounds?|lbs?|oz|qt|pt|g|kg|ml|cl|fl|l)\b[\s\d().,/–—-]*)+$", re.I)
    unquantified_start = re.compile(r"^(?:salt\b|(?:freshly\s+ground\s+)?(?:black\s+|white\s+)?pepper\b|oil\b|water\b|juice\s+of\b|pinch\s+of\b|handful\s+of\b)", re.I)
    groups = []
    for start, end in lines:
        value = text[start:end]
        quantified = bool(re.match(QUANTITY_START, value, re.I))
        previous = text[groups[-1][0]:groups[-1][1]] if groups else ""
        tail = previous.splitlines()[-1] if previous else ""
        qualifier = _qualifier_start(value) and not _heading_kind(value)
        previous_or = bool(re.search(r"\bor\s*$", tail, re.I))
        incomplete = (tail.rstrip().endswith((",", "-")) or bool(re.search(r"\bor\s*$", tail, re.I)) or
            previous.count("(") > previous.count(")") or bool(units_only.fullmatch(previous)) or
            bool(re.search(r"\b(?:whole|heavy|large|small|medium|finely|roughly|freshly|at room|extra[- ]virgin|all[- ]purpose)\s*$", tail, re.I)))
        continuation = groups and not _heading_kind(value) and (qualifier or previous_or or
            (not quantified and not unquantified_start.match(value) and incomplete))
        if continuation:
            groups[-1][1] = end
        else:
            groups.append([start, end])
    result = []
    active_scope = "optional" if _heading_kind(group or "") == "optional" else inherited_scope
    for start, end in groups:
        value = text[start:end]
        heading_kind = _heading_kind(value)
        if heading_kind == "ambiguous_optional":
            if result and result[-1]["optional"] is False:
                result[-1].update(optional=None, variant_scope="unresolved_optional_heading", presence_basis="ambiguous_optional_label")
            active_scope = "unresolved_optional_heading"
        elif heading_kind == "optional":
            active_scope = "optional"
        elif heading_kind == "boundary":
            active_scope = None
        # A subordinate example/substitute is not a requirement for every
        # named option. Preserve the printed primary formula separately.
        split = re.search(r",\s*such\s+as\b|\(\s*or\s+(?:substitute|use)\b", value, re.I)
        matched_exception = exception and value == exception["text"]
        if matched_exception:
            split_at = start + len(exception["primary_text"])
            segments = [(start, split_at, False), (split_at, end, True)]
        elif split:
            split_at = start + split.start()
            segments = [(start, split_at, False), (split_at, end, True)]
        else:
            segments = [(start, end, False)]
        for part_start, part_end, subordinate in segments:
            while part_start < part_end and text[part_start].isspace():
                part_start += 1
            while part_end > part_start and text[part_end - 1].isspace():
                part_end -= 1
            if part_start == part_end:
                continue
            part = text[part_start:part_end]
            optional, variant, basis = ingredient_presence(part, reviewed_role, group)
            if subordinate:
                optional, variant, basis = None, group or "alternative", "subordinate_source_alternative"
            elif matched_exception:
                basis = "source_bound_presence_exception"
            if heading_kind:
                optional, variant, basis = None, "heading", "source_heading"
            elif active_scope == "optional":
                optional, basis = True, "explicit_optional_heading"
            elif active_scope:
                optional, variant, basis = None, active_scope, "unresolved_heading_scope"
            result.append(dict(start=part_start, end=part_end, text=part, optional=optional,
                               variant_scope=variant, presence_basis=basis))
    return result


def node(key):
    namespace, record_id = key.split(":", 1)
    return {"entity": "recipe" if namespace == "recipe" else "knowledge", "id": record_id}


def revised(record):
    record["revision"] = digest({k: v for k, v in record.items() if k != "revision"})
    return record


def _review_ref(kind, record_id, value):
    return kind + ":" + record_id + ":" + digest(value)[:24]


class RecipeConverter:
    """One immutable frozen input tree, accessed using bounded record caches."""
    def __init__(self, baseline_root=DEFAULT_BASELINE, *, knowledge_resolver=None):
        self.root = Path(baseline_root)
        self.knowledge_resolver = knowledge_resolver
        self.data = self.root / "runtime" / "data"
        self.recipes = JsonlMapping(self.data / "recipes.jsonl")
        self.sections = JsonlMapping(self.data / "knowledge_sections.jsonl", cache_bytes=4 * 1024 * 1024)
        self._hashes, self._valid, self._revisions = {}, {}, {}
        self.structure = load_legacy(self.root, "recipe_structure").RecipeStructure(
            self.recipes, self.data / "recipe_structure.json")
        self.cooking_view = load_legacy(self.root, "cooking_view").cooking_view
        self.excerpt_source = load_legacy(self.root, "recipe_dependencies").excerpt_source
        self.labels = json.loads((self.data / "recipe_discovery_labels.json").read_text())["labels"]
        self.bundle = json.loads((self.data / "recipe_dependencies.json").read_text())
        if self.bundle.get("version") != "recipe-dependencies-2":
            raise ValueError("Expected accepted recipe-dependencies-2 review assets")
        self.methods = self.bundle.get("methods", {})
        self.reviews = dict(self.bundle["reviews"])
        self.reviews.update({"method:" + key: row for key, row in self.methods.items()})
        self.selected_section_ids = {key[8:] for key, row in self.reviews.items() if key.startswith("section:") and row.get("segments")}
        self.scoped_section_ids = {key[8:] for key, row in self.reviews.items() if key.startswith("section:") and not row.get("segments")}
        self._books = None
        self._supporting_link_cache = {}
        self._presence_exceptions = json.loads((Path(__file__).resolve().parents[1] / "evals/library-v2/recipes/presence-exceptions.json").read_text())["exceptions"]
        note_policy = json.loads((Path(__file__).resolve().parents[1] / "evals/library-v2/knowledge/supporting-note-scopes.json").read_text())
        if note_policy.get("default_disposition") != "inline_note":
            raise ValueError("Supporting notes require an explicit inline default")
        self._note_scopes = {(row["recipe_id"], row["note_index"]): row for row in note_policy["reviews"]}
        self._validated_note_scopes = set()

    def node(self, key):
        value = node(key)
        if key.startswith("section:") and key[8:] in self.selected_section_ids:
            value["id"] = "scoped:" + key[8:]
        return value

    def supporting_note_scope(self, raw, index):
        scope = self._note_scopes.get((raw["id"], index))
        if scope is None:
            return None
        cache_key = (raw["id"], index)
        if cache_key not in self._validated_note_scopes:
            note = raw["supporting_notes"][index]
            valid = (self.source_hash("recipe:" + raw["id"]) == scope["recipe_source_record_sha256"] and
                digest(note) == scope["note_sha256"] and len(note["blocks"]) == scope["source_block_count"] and
                self.source_hash("section:" + scope["source_section_id"]) == scope["source_section_record_sha256"])
            selected = set(scope["inline_block_indices"])
            context = [i for start, end in scope["context_block_ranges"] for i in range(start, end)]
            valid = valid and not selected.intersection(context) and selected.union(context) == set(range(len(note["blocks"])))
            for row in scope["inline_blocks"]:
                block = note["blocks"][row["index"]]
                valid = valid and digest(block) == row["block_sha256"] and hashlib.sha256(block["text"].encode()).hexdigest() == row["text_sha256"]
            if not valid:
                raise ValueError("Stale supporting-note scope; candidate held: " + raw["id"])
            self._validated_note_scopes.add(cache_key)
        return scope

    def source(self, key):
        namespace, record_id = key.split(":", 1)
        return self.recipes[record_id] if namespace == "recipe" else self.sections[record_id]

    def source_hash(self, key):
        if key not in self._hashes:
            self._hashes[key] = digest(self.source(key))
        return self._hashes[key]

    def valid_review(self, key):
        if key in self._valid:
            return self._valid[key]
        row = self.reviews.get(key)
        if row is None:
            self._valid[key] = False
            return False
        source_key = "recipe:" + row["source_recipe"] if key.startswith("method:") else key
        if source_key not in row.get("bindings", {}):
            raise ValueError("Dependency review lacks its source binding: " + key)
        valid = True
        for bound, expected in row["bindings"].items():
            try:
                valid = valid and self.source_hash(bound) == expected
            except KeyError:
                valid = False
        if row.get("assembly_hash") is not None:
            assembled = self.structure.rows.get(key[7:]) if key.startswith("recipe:") else None
            valid = valid and assembled is not None and digest(assembled) == row["assembly_hash"]
        if valid:
            if not row.get("scope") or not isinstance(row.get("limitations"), list):
                raise ValueError("Dependency scope/limitations missing: " + key)
            choices = {}
            for edge in row["edges"]:
                if edge["disposition"] not in {"required", "assembled", "optional", "choice", "unresolved"}:
                    raise ValueError("Invalid dependency disposition")
                if edge["relationship"] not in {"component", "method_reference", "variation_of", "related_source", "illustration"}:
                    raise ValueError("Invalid dependency relationship")
                if not edge.get("application") or not edge.get("evidence"):
                    raise ValueError("Dependency needs applicability and evidence")
                target = edge.get("target")
                if not target and edge["disposition"] in {"required", "assembled", "choice"}:
                    raise ValueError("Dependency target missing")
                if target:
                    target_key = target["kind"] + ":" + target["id"]
                    target_source = "recipe:" + self.methods[target["id"]]["source_recipe"] if target["kind"] == "method" else target_key
                    if target_source not in row["bindings"]:
                        raise ValueError("Unbound dependency target: " + target_key)
                if edge["disposition"] == "assembled" and not row.get("assembly_hash"):
                    raise ValueError("Assembled dependency lacks normalization hash")
                if edge["disposition"] == "choice":
                    if not edge.get("choice_group"):
                        raise ValueError("Required choice lacks a group")
                    choices.setdefault(edge["choice_group"], set()).add(target_key)
                elif edge.get("choice_group") is not None:
                    raise ValueError("Choice group on non-choice edge")
                for evidence in edge["evidence"]:
                    self._validate_evidence(evidence, row)
            if any(len(options) < 2 for options in choices.values()):
                raise ValueError("Required choice needs two distinct options")
            for evidence in row.get("segments", []):
                self._validate_evidence(evidence, row)
                if evidence["node"] != source_key:
                    raise ValueError("Scoped method segment crosses its source")
        self._valid[key] = valid
        return valid

    def _validate_evidence(self, evidence, row):
        if evidence["node"] not in row["bindings"]:
            raise ValueError("Unbound dependency evidence")
        value = at_path(self.source(evidence["node"]), evidence["path"])
        if not isinstance(value, str) or not evidence.get("quote") or evidence["quote"] not in value:
            raise ValueError("Dependency evidence is not an exact source excerpt")

    def coverage(self, key):
        row = self.reviews.get(key)
        if row and self.valid_review(key):
            return dict(status="reviewed_bounded", scope=row["scope"], limitations=row["limitations"])
        return dict(status="stale" if row else "unknown",
                    scope="Dependency coverage has not been reviewed against the current source.",
                    limitations=["Read the source for missing preparations; unknown coverage does not mean no dependencies."])

    def review_refs(self, record):
        refs = [_review_ref(key, record["id"], record[key]) for key in CORRECTIONS if record.get(key)]
        if record.get("review"):
            refs.append(_review_ref("source-review", record["id"], record["review"]))
        return refs

    def _citation_span(self, source_key, path, quote=None):
        record = self.source(source_key)
        # Use precisely the accepted resolver's citation fallback, including its
        # conservative attached-note behavior. No dirty-checkout fallback.
        if source_key.startswith("recipe:"):
            loc_source = self.excerpt_source(record, path)
            if len(path) == 5 and path[0] == "supporting_notes" and path[2] == "blocks":
                note_scope = self.supporting_note_scope(record, path[1])
                if note_scope:
                    for override in note_scope["citation_overrides"]:
                        if override["block_index"] == path[3]:
                            loc_source.pop("printed_page", None)
                            loc_source["printed_pages"] = override["printed_labels"]
            scope = "block" if loc_source.get("location_scope", "").startswith("selected ") else "item"
            cited = dict(record, source=loc_source)
            if path and path[0] == "supporting_notes":
                note = record["supporting_notes"][path[1]]
                for key in ("book_id", "book", "author"):
                    cited[key] = note.get(key) or loc_source.get(key) or record.get(key)
            cite = citation(cited, scope=scope)
        else:
            parent = at_path(record, path[:-1])
            locator = {}
            if isinstance(parent, dict):
                locator.update(parent.get("source") or {})
                locator.update({k: parent[k] for k in ("path", "pages", "pdf_page", "pdf_pages", "printed_page", "printed_pages", "member", "anchor") if parent.get(k)})
            cite = citation(record, locator, scope="section")
        span = source_span(record, path, quote, cite["id"], self.source_hash(source_key))
        return cite, span

    def _citation_spans(self, source_key, path, quote=None):
        cite, span = self._citation_span(source_key, path, quote)
        result = [(cite, span)]
        record = self.source(source_key)
        parent = at_path(record, path[:-1])
        if isinstance(parent, dict):
            for continued in parent.get("continued_sources", []):
                extra = citation(dict(record, source=continued), scope="block")
                if extra["id"] not in {c["id"] for c, _ in result}:
                    result.append((extra, dict(span, citation_id=extra["id"])))
        return result

    def accepted_view(self, rid):
        record = dict(self.structure.project(rid))
        record["normalized_sections"] = self.structure.sections(rid)
        return self.cooking_view(record)

    def supporting_context_link(self, raw, note):
        identity = digest([edition_id(raw), note])
        if identity not in self._supporting_link_cache:
            target_id = None
            candidate = note.get("section_id")
            if candidate and candidate in self.sections and note.get("blocks") == self.sections[candidate].get("blocks"):
                target_id = candidate
            link = dict(node=dict(entity="knowledge", id=target_id or "context:" + identity),
                        title=note.get("title") or "Attached source context", applicability="unreviewed")
            self._supporting_link_cache[identity] = link
        return deepcopy(self._supporting_link_cache[identity])

    def convert_recipe(self, rid):
        raw = self.recipes[rid]
        view = self.accepted_view(rid)
        source_hash = self.source_hash("recipe:" + rid)
        citations = OrderedDict()
        base_citation = citation(raw)
        citations[base_citation["id"]] = base_citation
        row = self.structure.rows.get(rid)
        refs = self.review_refs(raw)
        structure_ref = _review_ref("structure", rid, row) if row else None
        if structure_ref:
            refs.append(structure_ref)
        key = "recipe:" + rid
        dep = self.reviews.get(key)
        if dep:
            refs.append(_review_ref("dependency", key, dep))
        corrections = [ref for ref in refs if not ref.startswith(("dependency:", "structure:"))]
        base_basis = "accepted_interpretation" if raw.get("inferences") or raw.get("interpretations") else "accepted_correction" if any(raw.get(k) for k in CORRECTIONS[1:] if k != "inferences") else "normalization" if raw.get("normalizations") else "source"

        def block(text, role, identity, *, evidence=None, path=None, basis=None, applicability=None):
            spans = []
            if evidence:
                for ev in evidence:
                    if "fragment_id" in ev:
                        fragment = row["source_fragments"][ev["fragment_id"]]
                        cite = citation(dict(raw, source=fragment["source"]), scope="block")
                        citations[cite["id"]] = cite
                        # A source fragment is archived in the structure sidecar,
                        # not deceptively assigned to the recipe's source text.
                        span = source_span(row, ["source_fragments", ev["fragment_id"], "text"], ev["quote"], cite["id"])
                    else:
                        source_id = ev.get("recipe_id", rid)
                        pairs = self._citation_spans("recipe:" + source_id, ev["path"], ev.get("quote"))
                        for cite, span in pairs:
                            citations[cite["id"]] = cite
                            spans.append(span)
                        continue
                    spans.append(span)
            elif path is not None and text:
                for cite, span in self._citation_spans(key, path):
                    citations[cite["id"]] = cite
                    spans.append(span)
            value = dict(id="block:" + digest([rid, identity])[:32], kind="text", role=role,
                         text=text, basis=basis or base_basis, source_spans=spans,
                         evidence_refs=([structure_ref] if evidence and structure_ref else corrections if base_basis != "source" else []))
            if applicability:
                value["applicability"] = applicability
            return value

        groups = []
        carried_heading = None
        entries = (view.get("normalization") or {}).get("ingredients_metadata", [])
        metadata = {e["line_index"]: e for e in entries}
        for i, text in enumerate(view["ingredients"]):
            entry = row["ingredients"][i] if row else None
            title = metadata.get(i, {}).get("group")
            if not groups or groups[-1]["title"] != title:
                if _heading_kind(title or "") == "boundary":
                    carried_heading = None
                groups.append(dict(id="group:" + digest([rid, len(groups), title])[:24], title=title, blocks=[]))
            value = block(text, "ingredient", ["ingredients", i],
                          evidence=entry.get("evidence") if entry else None,
                          path=["ingredients", i] if not entry else None,
                          basis="normalization" if entry else None)
            value["group_id"] = groups[-1]["id"]
            role = entry.get("role") if entry else None
            exception = self._presence_exceptions.get(rid + ":" + str(i))
            if exception and (exception["source_record_sha256"] != source_hash or exception["text"] != text):
                raise ValueError("Stale ingredient presence exception: " + rid)
            if exception:
                presence_ref = _review_ref("ingredient-presence", rid, exception)
                value["evidence_refs"].append(presence_ref)
                if presence_ref not in refs:
                    refs.append(presence_ref)
            occurrences = ingredient_occurrences(text, role, title, exception=exception,
                inherited_scope=carried_heading["scope"] if carried_heading else None)
            if carried_heading:
                value["evidence_refs"].append(_review_ref("ingredient-heading-scope", rid, dict(
                    source_record_sha256=source_hash, **carried_heading)))
            for line in text.splitlines():
                heading_kind = _heading_kind(line)
                if heading_kind in {"optional", "ambiguous_optional"}:
                    carried_heading = dict(scope="unresolved_optional_heading", source_block=value["id"], source_text=text)
                elif heading_kind == "boundary":
                    carried_heading = None
            value["ingredient_occurrences"] = occurrences
            if len(occurrences) == 1 and "\n" not in text and "\r" not in text:
                optional, variant_scope = occurrences[0]["optional"], occurrences[0]["variant_scope"]
            else:
                optional, variant_scope = None, None
            value["ingredient"] = dict(identity=None, role="unknown", optional=optional, variant_scope=variant_scope)
            groups[-1]["blocks"].append(value)
        # A qualifier can itself have been extracted as another ingredient
        # block. Such an attachment cannot establish a required preceding
        # ingredient; retain both source strings and bind the uncertainty.
        flat_ingredients = [value for group in groups for value in group["blocks"]]
        for previous, current in zip(flat_ingredients, flat_ingredients[1:]):
            current_occurrences = current["ingredient_occurrences"]
            if not current_occurrences:
                continue
            first = current_occurrences[0]["text"]
            heading_kind = _heading_kind(first)
            trailing_or = bool(re.search(r"\bor\s*$", previous["text"], re.I))
            qualifier = _qualifier_start(first) and heading_kind != "optional" and heading_kind != "boundary"
            if qualifier or trailing_or:
                if previous["ingredient_occurrences"]:
                    previous_occurrences = previous["ingredient_occurrences"]
                    principal_index = len(previous_occurrences) - 1
                    while principal_index and previous_occurrences[principal_index]["presence_basis"] == "subordinate_source_alternative":
                        principal_index -= 1
                    occurrence = previous_occurrences[principal_index]
                    if occurrence["optional"] is False:
                        occurrence.update(optional=None, variant_scope="unresolved_qualifier", presence_basis="cross_block_qualifier_scope")
                        previous["ingredient"].update(optional=None, variant_scope="unresolved_qualifier")
                        previous["evidence_refs"].append(_review_ref("ingredient-qualifier-scope", rid,
                            [source_hash, current["id"], current["text"]]))
                if trailing_or and current_occurrences[0]["optional"] is False:
                    current_occurrences[0].update(optional=None, variant_scope="unresolved_qualifier", presence_basis="cross_block_qualifier_scope")
                    current["ingredient"].update(optional=None, variant_scope="unresolved_qualifier")
        methods = []
        for i, text in enumerate(view["instructions"]):
            entry = row["method"][i] if row and "method" in row else None
            methods.append(block(text, "method", ["instructions", i], evidence=entry.get("evidence") if entry else None,
                                 path=["instructions", i] if not entry else None, basis="normalization" if entry else None))
        headnotes = []
        if view["headnote"]:
            evidence = [ev for entry in row.get("headnote", []) for ev in entry["evidence"]] if row else None
            headnotes.append(block(view["headnote"], "headnote", ["headnote"], evidence=evidence,
                                   path=["headnote"] if not evidence else None, basis="normalization" if evidence else None))
        notes = []
        context_links = []
        for i, entry in enumerate(view.get("cooking_notes", [])):
            notes.append(block(entry["text"], "note", ["cooking_notes", i], evidence=entry["evidence"], basis="normalization", applicability=entry["applicability"]))
        for i, note in enumerate(raw.get("supporting_notes", [])):
            if not isinstance(note, dict):
                raise ValueError("Unrecognized supporting note shape")
            link = self.supporting_context_link(raw, note)
            note_scope = self.supporting_note_scope(raw, i)
            if note_scope:
                refs.extend([note_scope["review_ref"], _review_ref("supporting-note-scope", rid, note_scope)])
            if note_scope and note_scope["context_block_ranges"] and link not in context_links:
                context_links.append(link)
            for j, entry in enumerate(note.get("blocks", [])):
                text = entry.get("text") if isinstance(entry, dict) else entry
                if not isinstance(text, str):
                    raise ValueError("Unrecognized supporting note block")
                if text:
                    path = ["supporting_notes", i, "blocks", j] + (["text"] if isinstance(entry, dict) else [])
                    inline = note_scope is None or j in note_scope["inline_block_indices"]
                    value = block(text, "note" if inline else "context", path, path=path, applicability=(
                        "Optional foil-rack method cited by this recipe; the complete source subsection is retained." if note_scope and inline else
                        note.get("application") or note.get("application_qualification") or view["supporting_notes"][i].get("application_qualification")) or
                        "Attached source context; applicability to this recipe or variant has not been reviewed. Do not automatically add its ingredients or methods.")
                    if note_scope:
                        value["evidence_refs"].append(note_scope["review_ref"])
                    if isinstance(entry, dict) and entry.get("kind") == "table":
                        value.update(kind="table", rows=deepcopy(entry.get("rows", [])), header_rows=deepcopy(entry.get("header_rows", [])))
                    elif isinstance(entry, dict) and entry.get("kind") in {"image", "figure"}:
                        value.update(kind="figure", asset_refs=deepcopy(entry.get("assets", entry.get("asset_refs", []))))
                    value["group_id"] = link["node"]["id"]
                    notes.append(value)
        context = []
        for entry in view["additional_source_context"]["entries"]:
            path = entry["source_path"]
            value = at_path(raw, path)
            if isinstance(value, dict):
                path = path + ["text"]
            context.append(block(entry["text"], "context", path, path=path,
                applicability=view["additional_source_context"]["application_qualification"]))
        if view.get("unstructured_source_context") and not context:
            path = ["source_prose_excerpt"] if raw.get("source_prose_excerpt") else ["source_text"]
            context.append(block(view["unstructured_source_context"], "context", path, path=path,
                                 applicability="Unstructured retained source; complete recipe sections are not established."))
        uncertainties = []
        def uncertainty(code, message, affects, action="inspect_source"):
            if message:
                value = dict(code=code, message=str(message), affects=affects, action=action, citation_ids=[base_citation["id"]])
                if value not in uncertainties:
                    uncertainties.append(value)
        # Newer accepted imports also retain qualifications under their own
        # field. Preserve unique material warnings, including when the fields
        # diverge; the uncertainty accumulator removes exact duplicates.
        for field in ("warnings", "source_qualifications"):
            for warning in raw.get(field, []):
                uncertainty("source_warning", warning, ["recipe"])
        for limitation in view["normalization"].get("limitations", []):
            uncertainty("structure_limitation", limitation, ["ingredients", "method"], "source_gap")
        for limitation in view.get("parent_note_coverage", {}).get("limitations", []):
            uncertainty("note_coverage", limitation, ["notes"], "read_target")
        if raw.get("yield_qualification"):
            uncertainty("qualified_yield", raw["yield_qualification"].get("reason", str(raw["yield_qualification"])), ["yield"])
        for field, values in (("ingredients", view["ingredients"]), ("method", view["instructions"])):
            if not values:
                uncertainty("missing_" + field, "The source extraction does not establish a complete " + field + " section.", [field], "source_gap")
        coverage = self.coverage(key)
        if dep and self.valid_review(key):
            for edge in dep["edges"]:
                if edge["disposition"] in {"choice", "unresolved"}:
                    uncertainty("required_choice" if edge["disposition"] == "choice" else "unresolved_dependency", edge["application"], [edge.get("choice_group") or "dependencies"], "choose_alternative" if edge["disposition"] == "choice" else "read_target")
        labels = self.labels.get(rid)
        classification = dict(kind="unknown", dish_type="unknown", status="unknown", uncertain_fields=["kind", "dish_type"], evidence_ref=None)
        if labels:
            if labels.get("source_hash") == source_hash:
                classification.update(kind=labels["preparation_kind"], dish_type=labels["dish_form"], status="source_bound_assessment",
                    uncertain_fields=[{"preparation_kind": "kind", "dish_form": "dish_type"}[k] for k in labels["uncertain_fields"]],
                    evidence_ref=_review_ref("classification", rid, labels))
                refs.append(classification["evidence_ref"])
            else:
                classification["status"] = "stale"
        result = dict(schema_version=VERSION, entity="recipe", id=rid, edition_id=edition_id(raw), title=view["title"],
            aliases=deepcopy(raw.get("aliases", [])),
            **{"yield": dict(text=view.get("yield") or None, basis="accepted_correction" if raw.get("yield_qualification") else "source", evidence_refs=corrections if raw.get("yield_qualification") else [])},
            ingredient_groups=groups, method_blocks=methods, headnotes=headnotes, notes=notes, additional_context=context,
            citations=list(citations.values()), uncertainties=uncertainties,
            provenance=dict(source_record_sha256=source_hash, archive_record_sha256=source_hash, review_refs=refs),
            classification=classification, dependency_coverage=coverage, variant_exclusions=[], context_links=context_links)
        return revised(result)

    def iter_recipes(self, ids=None):
        for rid in self.recipes if ids is None else ids:
            yield self.convert_recipe(rid)

    def iter_supporting_context(self, ids=None):
        """New context targets only; exact existing sections are never duplicated."""
        seen = set()
        for rid in self.recipes if ids is None else ids:
            raw = self.recipes[rid]
            contexts = []
            for note in raw.get("supporting_notes", []):
                link = self.supporting_context_link(raw, note)
                target = link["node"]["id"]
                if target.startswith("context:") and target not in seen:
                    seen.add(target)
                    contexts.append((note, link))
            if not contexts:
                continue
            recipe = self.convert_recipe(rid)
            for note, link in contexts:
                blocks = [deepcopy(block) for block in recipe["notes"] if block.get("group_id") == link["node"]["id"]]
                for block in blocks:
                    block["role"] = "prose"
                citation_ids = {span["citation_id"] for block in blocks for span in block["source_spans"]}
                citations = [deepcopy(cite) for cite in recipe["citations"] if cite["id"] in citation_ids]
                if not citations:
                    citations = [citation(raw)]
                result = dict(schema_version=VERSION, entity="knowledge", id=link["node"]["id"],
                    edition_id=recipe["edition_id"], title=link["title"], kind="reference", heading_path=[link["title"]],
                    blocks=blocks, topics=[], uncertain_topics=[], topic_status="unknown", related_recipe_ids=[],
                    citations=citations,
                    uncertainties=[dict(code="unreviewed_applicability", message="Attached source context; applicability to a particular recipe or variant has not been reviewed.",
                        affects=[], action="inspect_source", citation_ids=[cite["id"] for cite in citations])],
                    provenance=deepcopy(recipe["provenance"]), dependency_coverage=dict(status="unknown",
                        scope="Retained attached source context; cooking applicability is unreviewed.", limitations=[]))
                yield revised(result)

    def canonical_revision(self, key):
        if key not in self._revisions:
            if key.startswith("recipe:"):
                record = self.convert_recipe(key[7:])
            else:
                if key in self.reviews:
                    record = self.convert_scoped_knowledge(key)
                elif self.knowledge_resolver:
                    record = self.knowledge_resolver(self.source(key))
                else:
                    from .knowledge import convert_knowledge
                    record = convert_knowledge(self.source(key))
            self._revisions[key] = record["revision"]
        return self._revisions[key]

    def iter_relationships(self, keys=None):
        for key in self.reviews if keys is None else keys:
            row = self.reviews[key]
            # Stale selections are held in the archive, never reminted as current
            # accepted evidence. Coverage on the source entity reports staleness.
            if not self.valid_review(key):
                continue
            review_ref = _review_ref("dependency", key, row)
            for index, edge in enumerate(row["edges"]):
                spans = [span for ev in edge["evidence"] for _, span in self._citation_spans(ev["node"], ev["path"], ev["quote"])]
                target = edge.get("target")
                target_key = target["kind"] + ":" + target["id"] if target else None
                bindings = [{"node": self.node(key), "revision": self.canonical_revision(key)}]
                if target_key:
                    bindings.append({"node": self.node(target_key), "revision": self.canonical_revision(target_key)})
                yield dict(schema_version=VERSION, id="relationship:" + digest([key, index, edge])[:32],
                    **{"from": self.node(key)}, target=self.node(target_key) if target_key else None,
                    kind=edge["relationship"], disposition=edge["disposition"], application=edge["application"],
                    choice_group=edge.get("choice_group"), evidence_spans=spans, review_ref=review_ref,
                    review_status="accepted", bindings=bindings, assembly_sha256=row.get("assembly_hash"))

    def convert_scoped_knowledge(self, key):
        row = self.reviews[key]
        valid = self.valid_review(key)
        source_key = "recipe:" + row["source_recipe"] if key.startswith("method:") else key
        raw = self.source(source_key)
        if key.startswith("section:") and not row.get("segments"):
            from .knowledge import convert_knowledge
            # The broad knowledge adapter owns table/figure fidelity. A complete
            # reviewed reference changes coverage, never its retained body.
            result = convert_knowledge(raw)
            result["dependency_coverage"] = self.coverage(key)
            result["provenance"]["review_refs"].append(_review_ref("dependency", key, row))
            return revised(result)
        cites = OrderedDict()
        base = citation(raw, scope="item" if key.startswith("method:") else "section")
        cites[base["id"]] = base
        blocks, included = [], []
        segments = row.get("segments")
        if not segments:
            segments = [dict(node=source_key, path=["blocks", i, "text"], quote=b["text"])
                        for i, b in enumerate(raw.get("blocks", [])) if b.get("text")]
        if valid:
            for i, ev in enumerate(segments):
                pairs = self._citation_spans(ev["node"], ev["path"], ev["quote"])
                for cite, span in pairs:
                    cites[cite["id"]] = cite
                    included.append(span)
                original = at_path(raw, ev["path"][:-1])
                kind = original.get("kind") if isinstance(original, dict) else None
                kind = kind if kind in {"table", "figure"} else "text"
                basis = "accepted_interpretation" if raw.get("inferences") or raw.get("interpretations") else "accepted_correction" if any(raw.get(k) for k in CORRECTIONS[1:]) else "normalization" if raw.get("normalizations") else "source"
                value = dict(id="block:" + digest([key, i])[:32], kind=kind, role="method", text=ev["quote"],
                             basis=basis, source_spans=[span for _, span in pairs], evidence_refs=[_review_ref("dependency", key, row)] + self.review_refs(raw))
                if kind == "table":
                    value.update(rows=deepcopy(original.get("rows", [])), header_rows=deepcopy(original.get("header_rows", [])))
                if kind == "figure":
                    value["asset_refs"] = deepcopy(original.get("asset_refs", []))
                blocks.append(value)
        excluded = []
        if valid and row.get("segments"):
            # Exact complements of selected source strings disclose exclusions
            # without adding sibling instructions to the selected item's body.
            paths = OrderedDict((tuple(ev["path"]), []) for ev in segments)
            for span in included:
                paths[tuple(span["legacy_path"])].append((span["start"], span["end"]))
            for path, intervals in paths.items():
                text = at_path(raw, path)
                end = 0
                for start, stop in sorted(intervals) + [(len(text), len(text))]:
                    if start > end:
                        quote = text[end:start]
                        cite, span = self._citation_span(source_key, list(path), quote)
                        span.update(start=end, end=start, text_sha256=hashlib.sha256(quote.encode()).hexdigest())
                        cites[cite["id"]] = cite
                        excluded.append(span)
                    end = max(end, stop)
        source_hash = self.source_hash(source_key)
        result = dict(schema_version=VERSION, entity="knowledge", id=self.node(key)["id"],
            edition_id=edition_id(raw), title=row.get("title") or raw["title"], kind="method" if key.startswith("method:") or row.get("segments") else "reference",
            heading_path=raw.get("heading_path", [raw["title"]]), blocks=blocks, topics=[], uncertain_topics=[], topic_status="unknown",
            related_recipe_ids=[row["source_recipe"]] if key.startswith("method:") else [],
            citations=list(cites.values()), uncertainties=[] if valid else [dict(code="stale_selection", message="The reviewed source changed; selected text is withheld.", affects=["method"], action="inspect_source", citation_ids=[base["id"]])],
            provenance=dict(source_record_sha256=source_hash, archive_record_sha256=source_hash, review_refs=[_review_ref("dependency", key, row)]),
            dependency_coverage=self.coverage(key))
        if valid:
            for warning in raw.get("warnings", []):
                result["uncertainties"].append(dict(code="source_warning", message=str(warning), affects=["method"],
                    action="inspect_source", citation_ids=[base["id"]]))
        if row.get("segments") and valid:
            result["selection"] = dict(source_node=node(source_key), scope=row["scope"], included_spans=included,
                excluded_spans=excluded, review_ref=_review_ref("dependency", key, row), source_binding_sha256=source_hash)
        return revised(result)

    def iter_scoped_knowledge(self):
        for key in self.reviews:
            if not key.startswith("recipe:"):
                yield self.convert_scoped_knowledge(key)

    def iter_mappings(self):
        for key in self.reviews:
            if key.startswith("recipe:"):
                continue
            row = self.reviews[key]
            source_key = "recipe:" + row["source_recipe"] if key.startswith("method:") else key
            item = self.convert_scoped_knowledge(key)
            namespace = "dependency_section" if key.startswith("section:") and row.get("segments") else key.split(":", 1)[0]
            yield dict(old_namespace=namespace, old_id=key.split(":", 1)[1],
                old_revision=self.source_hash(source_key), status="exact" if self.valid_review(key) else "stale",
                targets=[dict(node=self.node(key), source_spans=[span for b in item["blocks"] for span in b["source_spans"]])],
                review_ref=_review_ref("dependency", key, row))

    def iter_books(self):
        if self._books is None:
            books = OrderedDict()
            for mapping in (self.recipes, self.sections):
                for record_id in mapping:
                    record = mapping[record_id]
                    source = record.get("source") or {}
                    bid = edition_id(record)
                    title = record.get("book") or source.get("book") or "Unknown book"
                    author = record.get("author") or source.get("author")
                    if bid not in books:
                        books[bid] = dict(schema_version=VERSION, entity="book", id=bid, title=title, aliases=[],
                            authors=[author] if author else [], edition_label=None, isbn=None,
                            legacy_book_id=record.get("book_id") or source.get("book_id"), assets=[])
                    elif books[bid]["title"] != title or books[bid]["authors"] != ([author] if author else []):
                        raise ValueError("Conflicting book registry metadata: " + bid)
                    asset = dict(sha256=source.get("sha256"), format=citation(record)["format"], archive_ref=None)
                    if asset not in books[bid]["assets"]:
                        books[bid]["assets"].append(asset)
            self._books = [revised(book) for book in books.values()]
        yield from deepcopy(self._books)

    def field_ledger(self, rid):
        raw = self.recipes[rid]
        return dict(id=rid, archive_record_sha256=self.source_hash("recipe:" + rid),
            fields={key: "mapped" if key in MAPPED else "archive_retained" if key in ARCHIVED else "held_discrepancy" for key in raw},
            field_sha256={key: digest(value) for key, value in raw.items()},
            block_fields={key: "mapped" if key == "text" else "archive_retained"
                          for block in raw.get("blocks", []) if isinstance(block, dict) for key in block},
            discrepancies=[key for key in raw if key not in MAPPED | ARCHIVED],
            correction_provenance={key: _review_ref(key, rid, raw[key]) for key in CORRECTIONS if raw.get(key)})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", nargs="*")
    args = parser.parse_args(argv)
    converter = RecipeConverter(args.baseline)
    args.output.mkdir(parents=True, exist_ok=True)
    selected_keys = {"recipe:" + rid for rid in args.ids} if args.ids else None
    # Pilot closure contains every bound target needed to validate references.
    if selected_keys is not None:
        changed = True
        while changed:
            before = len(selected_keys)
            for key in list(selected_keys):
                row = converter.reviews.get(key, {})
                selected_keys.update(row.get("bindings", {}))
                selected_keys.update(edge["target"]["kind"] + ":" + edge["target"]["id"] for edge in row.get("edges", []) if edge.get("target"))
            changed = before != len(selected_keys)
    selected = {key[7:] for key in selected_keys if key.startswith("recipe:")} if selected_keys else None
    counts = {}
    streams = {"books": converter.iter_books(), "recipes": converter.iter_recipes(sorted(selected) if selected else None),
               "relationships": converter.iter_relationships(), "scoped-knowledge": converter.iter_scoped_knowledge(),
               "supporting-context": converter.iter_supporting_context(sorted(selected) if selected else None),
               "mappings": converter.iter_mappings(),
               "field-ledger": (converter.field_ledger(rid) for rid in sorted(selected) if selected) if selected else
                               (converter.field_ledger(rid) for rid in converter.recipes)}
    if selected_keys is not None:
        keys = [key for key in converter.reviews if key in selected_keys]
        streams["relationships"] = converter.iter_relationships(keys)
        streams["scoped-knowledge"] = (converter.convert_scoped_knowledge(key) for key in keys if not key.startswith("recipe:"))
        streams["mappings"] = (record for record in converter.iter_mappings()
            if ("section:" if record["old_namespace"] == "dependency_section" else record["old_namespace"] + ":") + record["old_id"] in selected_keys)
    for name, records in streams.items():
        count = 0
        with (args.output / (name + ".jsonl")).open("wb") as stream:
            for record in records:
                stream.write(encoded(record) + b"\n")
                count += 1
        counts[name] = count
    (args.output / "conversion-report.json").write_text(json.dumps(dict(counts=counts,
        baseline_receipt_sha256=hashlib.sha256((args.baseline / "restore-receipt.json").read_bytes()).hexdigest(),
        limitations=["Canonical conversion is not a new cooking review or complete-source certification.",
                     "Ingredient identities are intentionally unknown until a separately bound annotation adapter supplies them."]), indent=2) + "\n")
    print(json.dumps(counts, sort_keys=True))


if __name__ == "__main__":
    main()
