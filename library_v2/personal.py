"""Editable personal Markdown -> canonical v2 records, entirely offline.

Markdown is an authoring input, never overwritten by builds. Immutable source
bytes and migration provenance are archived alongside each build. This module
does not activate a runtime or claim source/dependency/cooking acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from importlib.metadata import version as package_version
import json
from pathlib import Path
import re
import shutil
import tempfile

from markdown_it import MarkdownIt
import yaml

from .contracts import canonical_json, revision, validate_content
from .recipes import ingredient_occurrences
from .store import compile_database

VERSION = "personal-markdown-1"
SECTIONS = {"Ingredients", "Method", "Headnote", "Notes", "Source", "Review notes"}
IDENTITY = re.compile(r"[a-z0-9][a-z0-9-]{0,100}\Z")


def sha(data):
    return hashlib.sha256(data).hexdigest()


class AuthoringError(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    """Safe YAML with duplicate keys rejected rather than silently replaced."""


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise AuthoringError("Metadata keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def parse_markdown(raw):
    """Use CommonMark structure; preserve inline source spelling and links.

    H1 is the title, H2 a named section, H3 a group/stage. Paragraphs and
    single-level lists are supported. Unsupported structures fail visibly,
    including HTML, images, code, nested lists and unknown section names.
    """
    try:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError as exc:
        raise AuthoringError("Recipes must be UTF-8") from exc
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise AuthoringError("Recipe needs YAML frontmatter")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise AuthoringError("Unclosed frontmatter")
    try:
        meta = yaml.load("".join(lines[1:end]), Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise AuthoringError("Invalid frontmatter") from exc
    if not isinstance(meta, dict) or set(meta) - {"id", "attribution", "yield", "aliases"}:
        raise AuthoringError("Allowed metadata: id, attribution, yield, aliases")
    if not isinstance(meta.get("id"), str) or not IDENTITY.fullmatch(meta["id"]):
        raise AuthoringError("id must be a stable lowercase slug")
    for key in ("attribution", "yield"):
        if meta.get(key) is not None and not isinstance(meta[key], str):
            raise AuthoringError(f"{key} must be text or null")
    if not isinstance(meta.get("aliases", []), list) or any(not isinstance(x, str) for x in meta.get("aliases", [])):
        raise AuthoringError("aliases must be a list of text")
    body = "".join(lines[end + 1:])
    env = {}
    tokens = MarkdownIt("commonmark").parse(body, env)
    if env.get("references"):
        raise AuthoringError("Use inline links, not reference link definitions")
    title = None
    section = None
    seen_sections = set()
    list_depth = 0
    rows = []
    allowed = {"heading_open", "heading_close", "inline", "paragraph_open", "paragraph_close",
               "bullet_list_open", "bullet_list_close", "ordered_list_open", "ordered_list_close",
               "list_item_open", "list_item_close"}
    for i, token in enumerate(tokens):
        if token.type not in allowed:
            raise AuthoringError(f"Unsupported Markdown structure: {token.type}")
        if token.type in {"bullet_list_open", "ordered_list_open"}:
            list_depth += 1
            if list_depth > 1:
                raise AuthoringError("Use H3 groups instead of nested lists")
        if token.type in {"bullet_list_close", "ordered_list_close"}:
            list_depth -= 1
        if token.type != "inline":
            continue
        if any(t.type in {"image", "html_inline"} for t in token.children or []):
            raise AuthoringError("Images/HTML need explicit source review; use a source link")
        previous = tokens[i - 1]
        value = token.content.strip()
        line = end + 2 + token.map[0]
        if previous.type == "heading_open":
            if list_depth:
                raise AuthoringError("Headings cannot be nested in lists")
            if previous.tag == "h1":
                if title is not None or rows or section is not None:
                    raise AuthoringError("Exactly one title must precede the recipe")
                title = value
            elif previous.tag == "h2":
                if title is None or value not in SECTIONS or value in seen_sections:
                    raise AuthoringError(f"Unknown, repeated or misplaced section: {value}")
                section = value
                seen_sections.add(value)
            elif previous.tag == "h3" and section in {"Ingredients", "Method", "Notes"}:
                rows.append(dict(section=section, kind="heading", text=value, line=line))
            else:
                raise AuthoringError("Use H3 for ingredient groups or method stages")
        else:
            if title is None or section is None:
                raise AuthoringError("Put prose under a named section")
            rows.append(dict(section=section, kind="text", text=value, line=line))
    if not title or not all(any(r["section"] == s and r["kind"] == "text" for r in rows)
                            for s in ("Ingredients", "Method")):
        raise AuthoringError("A title, ingredients and method are required")
    return meta, title, rows


def contained(root, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise AuthoringError("Expected a collection-relative path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise AuthoringError("Path escapes the collection")
    return path


def _seal(record):
    record["revision"] = revision(record)
    validate_content(record)
    return record


def convert_recipe(raw, relative, config, origin):
    meta, title, rows = parse_markdown(raw)
    rid = "personal:" + config["id"] + ":" + meta["id"]
    if len(rid) > 180:
        raise AuthoringError("Collection and recipe IDs together are too long")
    edition = "personal:" + config["id"]
    file_hash = sha(raw)
    citation_id = rid + ":source"
    citation = dict(id=citation_id, edition_id=edition, asset_sha256=file_hash,
                    book=config["title"], author=meta.get("attribution"), format="other",
                    printed_labels=[], pdf_file_pages=[], epub_member=None, epub_anchor=None,
                    location_scope="item", document=dict(path=relative, sha256=file_hash,
                    origin_url=origin.get("url") if origin else None))
    groups, methods, notes, headnotes, uncertainties = [], [], [], [], []
    occurrences = Counter()

    def block(row, role):
        text = row["text"]
        key = role + ":" + sha(text.encode())[:20]
        occurrences[key] += 1
        bid = rid + ":" + key + ":" + str(occurrences[key])
        result = dict(id=bid, kind="text", role=role, text=text, basis="source",
            source_spans=[dict(source_record_sha256=file_hash,
                source_block_id=f"line:{row['line']}", start=0, end=len(text),
                text_sha256=sha(text.encode()), citation_id=citation_id)], evidence_refs=[])
        if role == "ingredient":
            result["ingredient_occurrences"] = ingredient_occurrences(text)
        return result

    for row in rows:
        section = row["section"]
        if section == "Ingredients":
            if row["kind"] == "heading":
                groups.append(dict(id=rid + ":group:" + str(len(groups)), title=row["text"], blocks=[]))
            else:
                if not groups:
                    groups.append(dict(id=rid + ":group:0", title=None, blocks=[]))
                groups[-1]["blocks"].append(block(row, "ingredient"))
        elif section == "Review notes":
            uncertainties.append(dict(code="personal_source_gap", message=row["text"],
                affects=[rid], action="source_gap", citation_ids=[citation_id]))
        else:
            role = "heading" if row["kind"] == "heading" else {
                "Method": "method", "Headnote": "headnote", "Notes": "note", "Source": "note"}[section]
            target = methods if section == "Method" else headnotes if section == "Headnote" else notes
            target.append(block(row, role))
    if any(not g["blocks"] for g in groups):
        raise AuthoringError("Ingredient groups must not be empty")
    return _seal(dict(schema_version="library-v2-draft-1", entity="recipe", id=rid,
        edition_id=edition, title=title, aliases=meta.get("aliases", []),
        **{"yield": dict(text=meta.get("yield"), basis="source" if meta.get("yield") else "unknown", evidence_refs=[])},
        citations=[citation], ingredient_groups=groups, method_blocks=methods,
        headnotes=headnotes, notes=notes, additional_context=[], uncertainties=uncertainties,
        provenance=dict(source_record_sha256=file_hash, archive_record_sha256=file_hash,
                        review_refs=[]),
        classification=dict(kind="unknown", dish_type="unknown", status="unknown",
                            uncertain_fields=["kind", "dish_type"], evidence_ref=None),
        dependency_coverage=dict(status="unknown", scope="Personal Markdown; dependency review pending",
                                limitations=["An import is not evidence of personal cooking or a complete source review."]),
        variant_exclusions=[]))


def build(collection, output, previous=None):
    collection, output = Path(collection).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(collection):
        raise AuthoringError("Use a new build directory outside the editable collection")
    config_raw = (collection / "collection.json").read_bytes()
    config = json.loads(config_raw)
    if config.get("format") != VERSION or not IDENTITY.fullmatch(config.get("id", "")):
        raise AuthoringError("Unsupported collection format or identity")
    if not isinstance(config.get("title"), str) or not config["title"].strip():
        raise AuthoringError("Collection needs a title")
    sources = {}
    for relative, expected in config.get("archives", {}).items():
        data = contained(collection, relative).read_bytes()
        if sha(data) != expected:
            raise AuthoringError("Archived source changed: " + relative)
        sources[relative] = data
    records, inputs, raw_files = [], {}, {}
    for path in sorted((collection / "recipes").rglob("*.md")):
        relative = path.relative_to(collection).as_posix()
        raw = contained(collection, relative).read_bytes()
        meta, _, _ = parse_markdown(raw)
        origin = config.get("origins", {}).get(meta["id"])
        if origin and origin.get("archive") not in sources:
            raise AuthoringError("Origin needs a verified archive")
        record = convert_recipe(raw, relative, config, origin)
        if record["id"] in inputs:
            raise AuthoringError("Duplicate recipe ID: " + record["id"])
        records.append(record)
        inputs[record["id"]] = dict(path=relative, sha256=sha(raw), revision=record["revision"])
        raw_files[relative] = raw
    if not records:
        raise AuthoringError("No recipes found")
    collection_hash = sha(canonical_json(inputs).encode())
    edition = "personal:" + config["id"]
    book = _seal(dict(schema_version="library-v2-draft-1", entity="book", id=edition,
        title=config["title"], aliases=config.get("aliases", []), authors=[],
        edition_label="Editable personal collection", isbn=None, legacy_book_id=None,
        assets=[dict(sha256=sha(raw), format="other", archive_ref="sources/" + sha(raw) + ".md")
                for raw in raw_files.values()]))
    old = json.loads((Path(previous) / "manifest.json").read_text()) if previous else None
    if old and old["collection_id"] != config["id"]:
        raise AuthoringError("Previous build belongs to another collection")
    previous_inputs = old["inputs"] if old else {}
    delta = dict(added=sorted(set(inputs) - set(previous_inputs)),
        removed=sorted(set(previous_inputs) - set(inputs)),
        edited=sorted(k for k in inputs.keys() & previous_inputs.keys()
                      if inputs[k]["sha256"] != previous_inputs[k]["sha256"]),
        renamed=sorted(k for k in inputs.keys() & previous_inputs.keys()
                       if inputs[k]["path"] != previous_inputs[k]["path"]))
    from cookbook.import_review import pending_import_review
    pending = pending_import_review([dict(id=r["id"], book_id=edition, canonical_revision=r["revision"])
                                     for r in records], {edition: collection_hash})
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".personal-build-", dir=output.parent))
    try:
        (temporary / "canonical/recipes").mkdir(parents=True)
        (temporary / "sources").mkdir()
        for raw in raw_files.values():
            (temporary / "sources" / (sha(raw) + ".md")).write_bytes(raw)
        for relative, raw in {**sources, **raw_files, "collection.json": config_raw}.items():
            dest = temporary / "authoring" / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
        (temporary / "collection.json").write_bytes(config_raw)
        (temporary / "canonical/book.json").write_text(canonical_json(book) + "\n")
        for record in records:
            (temporary / "canonical/recipes" / (record["id"].split(":")[-1] + ".json")).write_text(canonical_json(record) + "\n")
        compiled = compile_database(temporary / "library.sqlite", books=[book], recipes=records,
                                    knowledge=[], chunks=[], metadata={"authoring_format": VERSION})
        manifest = dict(format=VERSION, collection_id=config["id"], source_sha256=collection_hash,
            inputs=inputs, delta=delta, compiled=compiled, import_review=pending,
            parser_versions={name:package_version(name) for name in ("markdown-it-py", "PyYAML", "jsonschema")},
            code_sha256={path.name:sha(path.read_bytes()) for path in
                         [Path(__file__), Path(__file__).with_name("recipes.py"),
                          Path(__file__).with_name("contracts.py"), Path(__file__).with_name("store.py"),
                          Path(__file__).with_name("reader.py")]},
            schema_sha256={path.name:sha(path.read_bytes()) for path in
                           sorted((Path(__file__).parent / "schemas").glob("*")) if path.is_file()},
            source_issues={r["id"]:r["uncertainties"] for r in records if r["uncertainties"]},
            acceptance="local_candidate_only", personally_tested=None)
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        if (collection / "collection.json").read_bytes() != config_raw or any(
                contained(collection, relative).read_bytes() != raw
                for relative, raw in {**sources, **raw_files}.items()):
            raise AuthoringError("Collection changed during build; retry in a fresh directory")
        current_paths = {p.relative_to(collection).as_posix() for p in (collection / "recipes").rglob("*.md")}
        if current_paths != set(raw_files):
            raise AuthoringError("Recipe files changed during build; retry")
        temporary.rename(output)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    result = build(args.collection, args.output, args.previous)
    print(json.dumps(dict(output=str(args.output.resolve()), recipes=len(result["inputs"]),
                          delta=result["delta"], acceptance=result["acceptance"]), indent=2))


if __name__ == "__main__":
    main()
