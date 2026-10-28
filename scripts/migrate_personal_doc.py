"""One-time migration of an inspected Google Doc table layout into Markdown.

Input is an archived get_document response plus a hash-bound layout profile.
No network calls, fuzzy recipe detection, source rewriting or existing-file writes.
"""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from library_v2.personal import VERSION, AuthoringError, parse_markdown, sha


def paragraphs(content):
    result = []
    for node in content:
        if "paragraph" not in node:
            raise AuthoringError("Unexpected non-paragraph inside a recipe cell")
        p = node["paragraph"]
        if any("textRun" not in e for e in p.get("elements", [])):
            raise AuthoringError("Embedded content requires visual review")
        text = "".join(e["textRun"]["content"] for e in p["elements"]).strip()
        links = [e["textRun"]["textStyle"]["link"]["url"] for e in p["elements"]
                 if e["textRun"].get("textStyle", {}).get("link", {}).get("url")]
        result.append(dict(text=text, links=links, bullet=bool(p.get("bullet")),
                           style=p.get("paragraphStyle", {})))
    return result


def migrate(source, profile, destination):
    raw = source.read_bytes()
    config = json.loads(profile.read_text())
    if sha(raw) != config["source_sha256"]:
        raise AuthoringError("Layout profile does not match this document snapshot")
    if destination.exists():
        raise FileExistsError(destination)
    doc = json.loads(raw)
    chunks, current = [], None
    for tab in doc["tabs"]:
        for node in tab["body"]["content"]:
            if "sectionBreak" in node:
                continue
            if "paragraph" in node:
                p = paragraphs([node])[0]
                if p["text"] and p["style"].get("namedStyleType") == "HEADING_2":
                    current = dict(title=p["text"], heading=p["style"]["headingId"],
                                   tab=tab["tabId"], preamble=[], tables=[])
                    chunks.append(current)
                elif p["text"]:
                    if current is None or current["tables"]:
                        raise AuthoringError("Unexpected text outside a recipe")
                    current["preamble"].append(p)
            elif "table" in node and current:
                current["tables"].append(node["table"])
            else:
                raise AuthoringError("Unexpected document block")
    if {c["heading"] for c in chunks} != set(config["recipes"]):
        raise AuthoringError("Profile must account for every recipe heading")
    output, origins, report = {}, {}, []
    for chunk in chunks:
        layout = config["recipes"][chunk["heading"]]
        slug = layout["id"]
        if len(chunk["tables"]) != 1:
            raise AuthoringError("Expected one ingredient/method table per recipe")
        table = chunk["tables"][0]
        if table["rows"] != 2 or table["columns"] != 2:
            raise AuthoringError("Expected a 2x2 table")
        headers = [paragraphs(c["content"])[0]["text"] for c in table["tableRows"][0]["tableCells"]]
        if headers != ["Ingredients", "Method"]:
            raise AuthoringError("Unexpected recipe columns")
        ingredients, method = [paragraphs(c["content"]) for c in table["tableRows"][1]["tableCells"]]
        attribution, yield_text, links = [], None, []
        for p in chunk["preamble"]:
            if re.match(r"^(Serves|Makes)\b", p["text"]):
                if yield_text:
                    raise AuthoringError("Multiple yields require review")
                yield_text = p["text"]
            else:
                attribution.append(p["text"])
            links.extend(p["links"])
        lines = ["---", "id: " + json.dumps(slug),
                 "attribution: " + json.dumps("; ".join(attribution) or None, ensure_ascii=False),
                 "yield: " + json.dumps(yield_text, ensure_ascii=False), "---", "",
                 "# " + chunk["title"], "", "## Ingredients", ""]
        expected = []
        for p in ingredients:
            links.extend(p["links"])
            if not p["text"]:
                continue
            expected.append(p["text"])
            prefix = "### " if p["text"] in layout["ingredient_headings"] else "- "
            lines.extend([prefix + p["text"], ""])
        lines.extend(["## Method", ""])
        for p in method:
            links.extend(p["links"])
            if not p["text"]:
                continue
            expected.append(p["text"])
            prefix = "### " if p["text"] in layout["method_headings"] else ""
            lines.extend([prefix + p["text"], ""])
        if links:
            lines.extend(["## Source", ""])
            lines.extend("- " + link for link in dict.fromkeys(links))
            lines.append("")
        if layout.get("review_notes"):
            lines.extend(["## Review notes", ""])
            lines.extend("- " + note for note in layout["review_notes"])
            lines.append("")
        content = ("\n".join(lines).rstrip() + "\n").encode()
        _, _, parsed = parse_markdown(content)
        retained = [r["text"] for r in parsed if r["section"] in {"Ingredients", "Method"}]
        if retained != expected:
            raise AuthoringError("Markdown conversion changed or omitted source text")
        relative = "recipes/" + slug + ".md"
        if relative in output:
            raise AuthoringError("Duplicate profile identity")
        output[relative] = content
        origins[slug] = dict(url=f"https://docs.google.com/document/d/{doc['documentId']}/edit?tab={chunk['tab']}#heading={chunk['heading']}",
                            archive="sources/" + sha(raw) + ".json", initial_markdown_sha256=sha(content))
        report.append(dict(id=slug, retained_cooking_paragraphs=len(expected),
                           source_links=len(set(links)), review_notes=layout.get("review_notes", [])))
    destination.mkdir(parents=True)
    (destination / "recipes").mkdir()
    (destination / "sources").mkdir()
    source_ref = "sources/" + sha(raw) + ".json"
    (destination / source_ref).write_bytes(raw)
    archives = {source_ref: sha(raw)}
    for relative, content in output.items():
        (destination / relative).write_bytes(content)
        initial = "sources/" + sha(content) + ".md"
        (destination / initial).write_bytes(content)
        archives[initial] = sha(content)
    profile_ref = "sources/" + sha(profile.read_bytes()) + "-layout.json"
    (destination / profile_ref).write_bytes(profile.read_bytes())
    archives[profile_ref] = sha(profile.read_bytes())
    collection = dict(format=VERSION, id=config["collection_id"], title=config["title"],
                      aliases=config.get("aliases", []), archives=archives, origins=origins)
    (destination / "collection.json").write_text(json.dumps(collection, ensure_ascii=False, indent=2) + "\n")
    (destination / "migration-report.json").write_text(json.dumps(dict(
        source_sha256=sha(raw), document_revision=doc["revisionId"], recipes=report,
        policy="Whitespace and Markdown grouping only; cooking text unchanged. Review notes are importer observations.",
        visuals="External links retained; external destinations/images not fetched or verified.",
        personally_tested=None), ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--profile", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    print(json.dumps(migrate(a.source, a.profile, a.output), indent=2))
