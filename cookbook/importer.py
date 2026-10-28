"""Deterministic EPUB adapters. Source text is never rewritten by a model."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

from lxml import etree, html
from .source_artifacts import is_source_artifact

BOOKS = {
    "dinner": ("All_About_Dinner_-_Molly_Stevens", "All About Dinner", "Molly Stevens"),
    "braising": ("All_About_Braising_-_Molly_Stevens", "All About Braising", "Molly Stevens"),
    "king": ("Cook_Like_a_King_-_Melissa_King", "Cook Like a King", "Melissa King"),
}
VERSION = "epub-adapters-3"

# A missing separator is much more likely than an improper culinary fraction.
# Limit inference to quantities with units so dates and reference numbers survive.
UNITS = r"(?:teaspoons?|tablespoons?|cups?|ounces?|pounds?|grams?|kilograms?|milliliters?|liters?|litres?|quarts?|pints?|gallons?|inches|inch|feet|foot|centimeters?|millimeters?|hours?|minutes?|seconds?|tsp|tbsp|oz|lb|lbs|kg|ml|cm|mm)"
JOINED_FRACTION = re.compile(
    r"(?<![\w./⁄])(?P<whole>[1-9]\d*)(?P<fraction>1[/⁄]2|[12][/⁄]3|[13][/⁄]4|[1357][/⁄]8)"
    r"(?=\s*(?:[-–—]\s*)?" + UNITS + r"\b|\s*[-–—]?\s*to\s+\d+(?:\s+\d+[/⁄]\d+)?\s*[-–—]?\s*" + UNITS + r"\b)",
    re.IGNORECASE,
)


def normalize_fractions(text):
    """Return readable quantities and an audit trail; never overwrite source text."""
    changes = []
    def replace(match):
        replacement = match["whole"] + " " + match["fraction"].replace("⁄", "/")
        changes.append({"original": match[0], "normalized": replacement,
                        "offset": match.start(), "rule": "inferred mixed fraction"})
        return replacement
    return JOINED_FRACTION.sub(replace, text), changes


def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def resolve(path, href):
    u = urlsplit(href)
    if u.scheme or u.netloc:
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(u.path))) if u.path else path


def read_epub(path):
    with ZipFile(path) as z:
        container = etree.fromstring(z.read("META-INF/container.xml"), etree.XMLParser(resolve_entities=False))
        opf = container.xpath('//*[local-name()="rootfile"]/@full-path')[0]
        package = etree.fromstring(z.read(opf), etree.XMLParser(resolve_entities=False))
        manifest = {n.get("id"): n.get("href") for n in package.xpath('//*[local-name()="manifest"]/*')}
        for ref in package.xpath('//*[local-name()="spine"]/*/@idref'):
            member = resolve(opf, manifest[ref])
            doc = html.fromstring(z.read(member), parser=html.HTMLParser(encoding="utf-8"))
            for n in doc.xpath("//script|//style"):
                n.drop_tree()
            # Styled ASCII fractions need a separator from a preceding whole number.
            # E.g. 2<span class="frac">1/2</span> must not become 21/2.
            for n in doc.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," frac ")]'):
                if re.fullmatch(r"\d+[⁄/]\d+", clean(n.text_content())):
                    previous = n.getprevious()
                    before = previous.tail if previous is not None else n.getparent().text
                    if before and before[-1:].isdigit():
                        if previous is not None: previous.tail = before + " "
                        else: n.getparent().text = before + " "
            yield member, doc


def blocks_from_doc(member, doc):
    blocks = []
    for n in doc.xpath("//p|//h1|//h2|//h3|//h4|//li|//figcaption"):
        # Do not duplicate paragraphs nested in list items.
        if n.tag == "li" and n.xpath(".//p|.//li"):
            continue
        text = clean(n.text_content())
        if not text or is_source_artifact(text):
            continue
        ids = ([n.get("id")] if n.get("id") else []) + n.xpath(".//*[@id]/@id")
        pages = n.xpath('.//*[@role="doc-pagebreak"]/@title')
        links = []
        for a in n.xpath(".//a[@href]"):
            href = a.get("href")
            target = resolve(member, href)
            if target:
                links.append({"text": clean(a.text_content()), "path": target, "anchor": unquote(urlsplit(href).fragment)})
        blocks.append({"text": text, "tag": n.tag, "classes": n.get("class", "").split(),
                       "path": member, "anchors": ids, "pages": pages, "links": links})
    return blocks


def is_title(book, b):
    c = set(b["classes"])
    return ((book == "dinner" and "h2r" in c)
            or (book == "braising" and "recipe-title" in c)
            or (book == "king" and b["tag"] == "h2" and bool(c & {"rt", "rt-alt1"})))


def classify(book, b, phase):
    c = set(b["classes"])
    if book == "dinner":
        if c & {"h3v", "h3r", "h3rt", "h3rt1", "h3s", "bxh", "h4"}:
            return "heading", "notes"
        if c & {"ing", "ingt", "ing1", "ingl"}:
            return ("ingredient" if phase != "notes" else "supplement_ingredient"), "ingredients" if phase != "notes" else phase
        if c & {"ing-h", "ingh"}:
            return "ingredient_group", phase
        if c & {"step", "step1"}:
            return ("instruction" if phase != "notes" else "note"), "instructions" if phase != "notes" else phase
        if "ser" in c:
            return "yield", phase
    elif book == "braising":
        if "variation" in c:
            if b["text"].upper().startswith("VARIATION"):
                return "heading", "notes"
            return "ingredient_group", phase
        if c & {"box-title", "box-title1", "box-title-a", "stage-heading", "normal-heading"}:
            return "heading", phase
        if "recipe-stitle" in c:
            return "subtitle", phase
        if c & {"recipe-ingred", "recipe-ingred-sp"}:
            return ("ingredient" if phase != "notes" else "supplement_ingredient"), phase
        if "serves" in c:
            return "yield", phase
        if c & {"ded1", "recipe-method-indent"} or ("noindent" in c and re.match(r"^\d+\.", b["text"])):
            return ("instruction" if phase != "notes" else "note"), "instructions" if phase != "notes" else phase
        if c & {"recipe-head-note", "recipe-head-noteind"}:
            return "headnote", phase
        if c & {"wine-notes", "wine-ornament", "wrk-ah", "wrk-ahtxt", "wrk-ahtxt-indent"}:
            return "note", phase
    elif book == "king":
        if "r1t" in c:
            return "heading", phase
        if c & {"ril", "rilf", "r1il"}:
            return ("ingredient" if phase != "component" else "supplement_ingredient"), phase
        if any(x.startswith("rilh") for x in c):
            return "ingredient_group", phase
        if any(x.startswith("ry-") for x in c):
            return "yield", phase
        if c & {"rp", "r1p"}:
            return ("instruction" if phase != "component" else "note"), phase if phase == "component" else "instructions"
        if c & {"rhnf", "rhn"}:
            return "headnote", phase
        if c & {"rv", "reqf"} or any(x.startswith(("rvh", "rn")) for x in c):
            return "note", phase
    if phase == "intro":
        return "headnote", phase
    if phase == "ingredients":
        return "instruction", "instructions"
    return ("instruction" if phase == "instructions" else "note"), phase


def import_book(book, source_dir):
    stem, title, author = BOOKS[book]
    path = source_dir / ("_OceanofPDF.com_" + stem + ".epub")
    if not path.exists():
        raise FileNotFoundError(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    recipes, supporting, page_lookup = [], [], {}
    for member, doc in read_epub(path):
        # Braising's chapter recipe lists carry original print-page references.
        if book == "braising":
            for p in doc.xpath('//p[@class="recipe-list" or @class="recipe-list1"]'):
                a, number = p.xpath(".//a[@href]"), p.xpath(".//b")
                if a and number:
                    page_lookup[(resolve(member, a[0].get("href")), urlsplit(a[0].get("href")).fragment)] = clean(number[0].text_content())
        blocks = blocks_from_doc(member, doc)
        starts = [i for i, b in enumerate(blocks) if is_title(book, b)]
        if not starts:
            if book == "dinner" and "/Recipes" in member and blocks:
                supporting.append({"book_id": book, "path": member, "title": blocks[0]["text"], "blocks": blocks})
            continue
        for ix, start in enumerate(starts):
            end = starts[ix + 1] if ix + 1 < len(starts) else len(blocks)
            section = blocks[start:end]
            head = section[0]
            anchor = head["anchors"][0] if head["anchors"] else ""
            key = book + "|" + member + "|" + anchor + "|" + head["text"]
            rid = hashlib.sha256(key.encode()).hexdigest()[:16]
            phase = "intro"
            for b in section[1:]:
                b["kind"], phase = classify(book, b, phase)
            head["kind"] = "title"
            raw = "\n".join(b["text"] for b in section)
            normalizations = []
            for block_index, b in enumerate(section[1:], 1):
                normalized, changes = normalize_fractions(b["text"])
                if changes:
                    b["source_text"] = b["text"]
                    b["text"] = normalized
                    normalizations.extend({**c, "block_index": block_index} for c in changes)
            ingredients = [b["text"] for b in section if b["kind"] == "ingredient"]
            instructions = [b["text"] for b in section if b["kind"] == "instruction"]
            notes = [b["text"] for b in section if b["kind"] == "headnote"]
            warnings = []
            if not ingredients: warnings.append("No separate ingredient list identified; inspect source.")
            if not instructions: warnings.append("No main instructions identified; inspect source.")
            ambiguous = [b["text"] for b in section if re.search(r"(?<!\d)\d{2,}[/⁄][2348](?!\d)", b["text"])]
            if ambiguous: warnings.append("Ambiguous fraction in source; verify: " + " | ".join(ambiguous))
            if "\x00" in raw or "�" in raw: warnings.append("Unexpected replacement/control character.")
            page = next(iter(head["pages"]), None)
            if not page and book == "braising":
                page = next((page_lookup[(member, a)] for a in head["anchors"] if (member, a) in page_lookup), None)
            recipes.append({"id": rid, "book_id": book, "book": title, "author": author,
                            "title": head["text"], "ingredients": ingredients, "instructions": instructions,
                            "headnote": "\n".join(notes), "yield": next((b["text"] for b in section if b["kind"] == "yield"), None),
                            "blocks": section, "source_text": raw, "warnings": warnings, "normalizations": normalizations,
                            "source": {"file": str(path.resolve()), "sha256": digest, "member": member,
                                       "anchor": anchor, "printed_page": page, "extractor": VERSION}})
    return recipes, supporting


def import_library(source_dir, output_dir):
    existing = output_dir / 'recipes.jsonl'
    if existing.exists():
        extra = {r['book_id'] for r in load_recipes(output_dir)} - set(BOOKS)
        if extra:
            raise ValueError('Pilot import would discard expanded books. Use a fresh candidate directory and the documented EPUB batch workflow.')
    output_dir.mkdir(parents=True, exist_ok=True)
    from .epub_index import capture, save
    all_recipes, supporting, book_indexes = [], [], []
    for book in BOOKS:
        recipes, extra = import_book(book, source_dir)
        all_recipes.extend(recipes)
        supporting.extend(extra)
        stem, title, author = BOOKS[book]
        book_indexes.append(capture(source_dir / ('_OceanofPDF.com_' + stem + '.epub'), book, title, author, recipes))
    # Link source cross-references to records, including component anchors.
    anchors, members = {}, {}
    for r in all_recipes:
        members.setdefault((r["book_id"], r["source"]["member"]), []).append(r["id"])
        for b in r["blocks"]:
            for a in b["anchors"]:
                anchors[(r["book_id"], b["path"], a)] = r["id"]
    support_lookup = {(s["book_id"], s["path"]): s for s in supporting}
    for r in all_recipes:
        refs = []
        for b in r["blocks"]:
            for ref in b["links"]:
                key = (r["book_id"], ref["path"])
                target = anchors.get((*key, ref["anchor"]))
                if not target and not ref["anchor"] and len(members.get(key, [])) == 1:
                    target = members[key][0]
                refs.append({**ref, "recipe_id": target, "supporting_note": key in support_lookup})
        r["references"] = list({(x["path"], x["anchor"]): x for x in refs}.values())
        r["supporting_notes"] = [support_lookup[(r["book_id"], x["path"])] for x in r["references"] if x["supporting_note"]]
    with (output_dir / "recipes.jsonl").open("w") as f:
        for r in all_recipes: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (output_dir / "supporting_notes.json").write_text(json.dumps(supporting, ensure_ascii=False, indent=2))
    summary = {"extractor": VERSION, "recipes": len(all_recipes), "by_book": dict(Counter(r["book"] for r in all_recipes)),
               "supporting_notes": len(supporting), "recipes_with_warnings": [{"id": r["id"], "title": r["title"], "warnings": r["warnings"]} for r in all_recipes if r["warnings"]],
               "with_print_page": sum(bool(r["source"]["printed_page"]) for r in all_recipes)}
    summary["fraction_normalizations"] = sum(len(r["normalizations"]) for r in all_recipes)
    summary['book_indexes'] = [bundle['report'] for bundle in book_indexes]
    summary["recipes_with_normalizations"] = [{"id": r["id"], "title": r["title"], "normalizations": r["normalizations"]} for r in all_recipes if r["normalizations"]]
    (output_dir / "import_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    for bundle in book_indexes: save(bundle, output_dir)
    return summary


def load_recipes(data_dir):
    return [json.loads(line) for line in (data_dir / "recipes.jsonl").read_text().splitlines() if line.strip()]
