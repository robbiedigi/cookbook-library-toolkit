"""Edition-bound HTML adapter; source prose is never an inferred ingredient list."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.request import Request, urlopen

from lxml import html
from library_v2.contracts import revision, validate_content
from library_v2.store import compile_database

PROFILE_PATH = Path(__file__).with_name("sources.json")
MAX_DOWNLOAD = 10 * 1024 * 1024


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def profiles():
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))["books"]


def source_bytes(profile, cache, offline=False):
    """Download only registered sources; fail closed if upstream bytes change."""
    cache = Path(cache)
    path = cache / (profile["gutenberg_id"] + ".html")
    if path.exists():
        raw = path.read_bytes()
    elif offline:
        raise FileNotFoundError(f"Missing cached source: {path}")
    else:
        request = Request(profile["download_url"], headers={"User-Agent": "CookbookEvidenceLibrary/0.1 (public-domain demonstration)"})
        with urlopen(request, timeout=45) as response:
            raw = response.read(MAX_DOWNLOAD + 1)
    if len(raw) > MAX_DOWNLOAD or sha(raw) != profile["sha256"]:
        raise ValueError(f"Source hash mismatch for {profile['gutenberg_id']}; inspect the new edition before updating sources.json.")
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return raw


def text_content(element):
    """Decode HTML and collapse layout whitespace; page labels stay in locators."""
    node = copy.deepcopy(element)
    for marker in node.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," pagenum ")]'):
        marker.drop_tree()
    for br in node.xpath('.//br'):
        br.tail = "\n" + (br.tail or "")
    return " ".join(node.text_content().split())


def extract_sections(raw, profile):
    """Explicit chapter bounds avoid TOC, license, medical and household material."""
    tree = html.fromstring(raw)
    body = tree.find("body")
    if body is None:
        raise ValueError("Source has no body")
    active = False
    started = stopped = False
    chapter = ""
    current = None
    sections = []
    page = None
    for node in body:
        if node.tag == "h2":
            heading = text_content(node)
            if heading == profile["end_before"]:
                stopped = True
                break
            if heading == profile["start_at"]:
                active = started = True
            if active:
                chapter = heading
        if not active:
            continue
        labels = node.xpath('.//*[starts-with(@id,"Page_")]/@id')
        before_page = page
        if labels:
            page = labels[-1].removeprefix("Page_")
        if node.tag in ("h2", "h3"):
            anchors = node.xpath('.//*[@id]/@id')
            if node.get("id"):
                anchors.insert(0, node.get("id"))
            current = dict(title=text_content(node), chapter=chapter,
                           xpath=node.getroottree().getpath(node),
                           anchor=anchors[0] if anchors else ("Page_" + page if page else None),
                           blocks=[], printed_labels=[])
            sections.append(current)
        elif current is not None and node.tag not in ("hr",):
            text = text_content(node)
            if not text:
                continue
            if node.tag not in ("p", "div") or node.xpath('.//table|.//img'):
                raise ValueError(f"Unsupported source structure at {node.getroottree().getpath(node)}")
            current["blocks"].append(dict(text=text, xpath=node.getroottree().getpath(node)))
            for label in ([before_page] if before_page else []) + [v.removeprefix("Page_") for v in labels]:
                if label not in current["printed_labels"]:
                    current["printed_labels"].append(label)
    if not started or not stopped:
        raise ValueError("Source chapter boundaries changed")
    return [section for section in sections if section["blocks"]]


def signed(record):
    record["revision"] = revision(record)
    validate_content(record)
    return record


def canonical_records(raw, profile):
    source_hash = sha(raw)
    # Content-bound edition IDs prevent cursors/records silently crossing editions.
    edition = f"pg:{profile['gutenberg_id']}:{source_hash[:12]}"
    book = signed(dict(schema_version="library-v2-draft-1", entity="book", id=edition,
        title=profile["title"], aliases=["pg:" + profile["gutenberg_id"]], authors=[profile["author"]],
        edition_label=profile["edition"], isbn=None, legacy_book_id=None,
        assets=[dict(sha256=source_hash, format="other", archive_ref="sources/" + profile["gutenberg_id"] + ".html")]))
    knowledge, recipes, extraction = [], [], []
    selected = set(profile["recipe_headings"])
    found = set()
    for section in extract_sections(raw, profile):
        section_id = edition + ":" + sha(section["xpath"].encode())[:16]
        citation_id = section_id + ":citation"
        source_url = profile["download_url"] + ("#" + section["anchor"] if section["anchor"] else "")
        source_path = "sources/" + profile["gutenberg_id"] + ".html"
        citation = dict(id=citation_id, edition_id=edition, asset_sha256=source_hash,
            book=profile["title"], author=profile["author"], format="other",
            printed_labels=section["printed_labels"], pdf_file_pages=[], epub_member=None,
            epub_anchor=None, location_scope="item",
            document=dict(path=source_path + " :: " + section["xpath"], sha256=source_hash, origin_url=source_url))
        blocks = []
        for index, item in enumerate(section["blocks"]):
            block_id = section_id + f":p{index}"
            text = item["text"]
            blocks.append(dict(id=block_id, kind="text", role="prose", text=text, basis="source",
                evidence_refs=[], source_spans=[dict(source_record_sha256=source_hash,
                    source_block_id=item["xpath"], start=0, end=len(text), text_sha256=sha(text.encode()), citation_id=citation_id)]))
        archive = dict(id=section_id, source=source_path, source_sha256=source_hash, **section)
        extraction.append(archive)
        common = dict(schema_version="library-v2-draft-1", edition_id=edition, title=section["title"],
            citations=[citation], uncertainties=[dict(code="historical_source", message="Historical text; not reviewed as modern cooking or food-safety guidance.",
                affects=[], action="inspect_source", citation_ids=[citation_id])],
            provenance=dict(source_record_sha256=source_hash, archive_record_sha256=revision(archive), review_refs=[]),
            dependency_coverage=dict(status="unknown", scope="Public demo: dependency applicability has not been reviewed.", limitations=[]))
        knowledge.append(signed(dict(copy.deepcopy(common), entity="knowledge", id=section_id + ":passage",
            kind="passage", heading_path=[section["chapter"], section["title"]], blocks=blocks,
            topics=[], uncertain_topics=[], topic_status="unknown", related_recipe_ids=[])))
        if section["title"] in selected:
            if section["title"] in found:
                raise ValueError("Ambiguous selected heading: " + section["title"])
            found.add(section["title"])
            methods = copy.deepcopy(blocks)
            for block in methods:
                block["role"] = "method"
            recipe = dict(copy.deepcopy(common), entity="recipe", id=section_id + ":recipe", aliases=[],
                ingredient_groups=[], method_blocks=methods, headnotes=[], notes=[], additional_context=[],
                classification=dict(kind="unknown", dish_type="unknown", status="unknown", uncertain_fields=["kind", "dish_type"], evidence_ref=None),
                variant_exclusions=[], **{"yield": dict(text=None, basis="unknown", evidence_refs=[])})
            recipe["uncertainties"].append(dict(code="prose_recipe", message="Ingredients and method remain together in source prose; structured ingredient filters and yields are unavailable. Completeness and dependencies are unreviewed.",
                affects=[], action="inspect_source", citation_ids=[citation_id]))
            recipes.append(signed(recipe))
    if found != selected:
        raise ValueError("Selected recipe headings missing: " + repr(sorted(selected - found)))
    return book, recipes, knowledge, extraction


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(output, cache, offline=False):
    """Stage a complete new snapshot. Never replace a working library."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Choose a fresh output directory: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".cookbook-build-", dir=output.parent) as temporary:
        stage = Path(temporary) / "library"
        stage.mkdir()
        (stage / "sources").mkdir()
        books, recipes, knowledge, extraction = [], [], [], []
        for profile in profiles():
            raw = source_bytes(profile, cache, offline)
            (stage / "sources" / (profile["gutenberg_id"] + ".html")).write_bytes(raw)
            book, rs, ks, xs = canonical_records(raw, profile)
            books.append(book); recipes.extend(rs); knowledge.extend(ks); extraction.extend(xs)
        for name, rows in (("books", books), ("recipes", recipes), ("knowledge", knowledge), ("extraction", extraction)):
            with (stage / (name + ".jsonl")).open("w", encoding="utf-8") as stream:
                for row in rows:
                    stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        report = compile_database(stage / "library.sqlite", books=books, recipes=recipes, knowledge=knowledge,
            metadata={"importer": "gutenberg-html-v1", "source_manifest_sha256": sha(PROFILE_PATH.read_bytes())})
        write_json(stage / "build-report.json", report)
        shutil.copyfile(PROFILE_PATH, stage / "sources.json")
        stage.rename(output)
    return report
