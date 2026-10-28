"""Local, source-preserving book passages and hybrid retrieval."""
import hashlib
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

import numpy as np

from .importer import clean, read_epub, resolve
from .source_artifacts import is_source_artifact
from .search import EMBED_MODEL, STOP, embedder, words

VERSION = "knowledge-1"
EPUBS = [("salt-fat-acid-heat", "Salt_Fat_Acid", "Salt, Fat, Acid, Heat", "Samin Nosrat"),
         ("mcgee", "On_Food_and_Cooking", "On Food and Cooking", "Harold McGee")]
# Explicit bounded pilots: one-based PDF page indices, not printed page labels.
PDFS = [("thai-food", "Thai_Food", "Thai Food", "David Thompson", range(328, 350)),
        ("professional-chef", "The_Professional_Chef", "The Professional Chef, 9th edition", "The Culinary Institute of America", range(592, 620))]


def stable(prefix, value):
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:16]


def source_info(path, book_id, title, author, format):
    return dict(book_id=book_id, book=title, author=author, file=path.name, format=format,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(), extractor=VERSION)


def epub_sections(path, book_id, title, author):
    source = source_info(path, book_id, title, author, "epub")
    docs = list(read_epub(path))
    # Internal chapter lists identify McGee's real subsection headings reliably.
    targets = {}
    for member, doc in docs:
        for a in doc.xpath('//a[contains(@href,"#")]'):
            href = a.get("href"); label = clean(a.text_content())
            if 3 <= len(label) <= 160:
                targets[(resolve(member, href), href.split("#", 1)[1])] = label
    sections, current, hierarchy, page = [], None, [], None
    for member, doc in docs:
        match = re.search(r"(?:text|split_)(\d+)\.html$", member)
        if not match:
            continue
        number = int(match[1])
        if (book_id == "mcgee" and not 9 <= number <= 31) or (book_id != "mcgee" and not 8 <= number <= 206):
            continue  # Exclude publisher front matter, index and bibliography.
        for br in doc.xpath('//br'):
            br.tail = " " + (br.tail or "")
        nodes = doc.xpath('//body//*[self::p or self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::table or self::li or self::blockquote or self::img]')
        for ordinal, node in enumerate(nodes):
            if node.xpath('ancestor::table') or (node.tag in ("li", "blockquote") and node.xpath('.//p|.//li|.//table')):
                continue
            if node.tag == "img" and node.xpath('ancestor::p'):
                continue
            text = clean(node.text_content())
            ids = ([node.get("id")] if node.get("id") else []) + node.xpath('.//*[@id]/@id')
            for anchor in ids:
                if re.fullmatch(r"page_\d+", anchor):
                    page = anchor.split("_")[1]
            loc = {**source, "member": member, "anchor": ids[0] if ids else None,
                   "element_index": ordinal, "printed_page": page, "pdf_page": None}
            if is_source_artifact(text):
                continue
            links = node.xpath('.//a[@href]')
            if links and clean("".join(a.text_content() for a in links)) == text:
                continue  # Navigation duplicates, not substantive content.
            level = int(node.tag[1]) if re.fullmatch(r"h[1-5]", node.tag) else None
            cls = node.get("class", "")
            if book_id == "mcgee":
                if cls == "calibre25": level = 1
                elif cls == "calibre26" or (cls == "calibre2" and current and re.fullmatch(r"Chapter \d+", current["title"]) and len(text) < 100): level = 2
                elif cls == "calibre29" and len(text) < 160: level = 3
                elif any(clean(targets.get((member, a), "")) == text for a in ids) and text and len(text) < 160: level = 4
                elif cls in ("calibre8", "calibre18") and len(text) < 160: level = 3
            if level and text:
                hierarchy = [(l, t) for l, t in hierarchy if l < level] + [(level, text)]
                current = dict(id=stable("s_", book_id + member + str(ordinal) + text),
                               title=text, headings=[t for _, t in hierarchy], source=loc,
                               blocks=[], flags=[])
                sections.append(current)
                continue
            if current is None:
                continue
            assets = node.xpath('.//img/@src') + ([node.get("src")] if node.tag == "img" else [])
            if assets:
                current["blocks"].append(dict(kind="image", text=text,
                    assets=[resolve(member, x) for x in assets], source=loc))
                if "Images retained by source reference; visual content is not transcribed." not in current["flags"]:
                    current["flags"].append("Images retained by source reference; visual content is not transcribed.")
                if not text:
                    continue
            if node.tag == "table":
                rows = [[clean(c.text_content()) for c in row.xpath('./td|./th')] for row in node.xpath('./tr|./tbody/tr|./thead/tr')]
                table_text = "\n".join(" | ".join(row) for row in rows)
                if current["blocks"] and current["blocks"][-1]["kind"] == "table":
                    b = current["blocks"][-1]; b["rows"].extend(rows); b["text"] += "\n" + table_text
                    b.setdefault("continued_sources", []).append(loc)
                else:
                    current["blocks"].append(dict(kind="table", text=table_text, rows=rows, source=loc))
            elif text:
                current["blocks"].append(dict(kind="text", text=text, source=loc))
    return [s for s in sections if any(b["text"] for b in s["blocks"])]


def pdf_sections(path, book_id, title, author, pages):
    import pdfplumber
    source = source_info(path, book_id, title, author, "pdf")
    sections, audit = [], []
    with pdfplumber.open(path) as pdf:
        for number in pages:
            page = pdf.pages[number - 1]
            # Fixed layouts selected for this pilot; do not generalize to other books.
            w, h = page.width, page.height
            bounds = [(0, 0, w, h * .94)]
            if book_id == "professional-chef":
                # Recipe gutters move between recto/verso pages; chapter prose
                # has no gutter. The printed vertical rule identifies the split.
                rules = [line["x0"] for line in page.lines if abs(line["x0"]-line["x1"]) < 1
                         and line["height"] > h * .7 and w * .4 < line["x0"] < w * .6]
                edges = [0, *sorted(set(rules)), w]
                bounds = [(left, 0, right, h * .92) for left, right in zip(edges, edges[1:])]
                # These two formula pages have three ingredient columns above
                # an overlapping method sidebar. Bounds checked against renders.
                if number in (596, 601):
                    x0, x1, x2, x3, bottom, side = ((.145,.408,.663,.92,.26,.405)
                        if number == 596 else (.085,.348,.603,.855,.285,.345))
                    bounds = [(w*x0, h*.065, w*x3, h*.115)]
                    bounds += [(w*a, h*.115, w*b, h*bottom) for a,b in zip((x0,x1,x2),(x1,x2,x3))]
                    bounds += [(0, h*bottom, w*side, h*.90), (w*side, h*bottom, w*x3, h*.90)]
            foot = page.crop((0, h * .94, w, h)).extract_text() or ""
            labels = re.findall(r"\b\d{1,4}\b", foot)
            printed = labels[-1] if labels else None
            loc = {**source, "pdf_page": number, "printed_page": printed, "member": None, "anchor": None}
            blocks = []
            for column, bbox in enumerate(bounds):
                crop = page.crop(bbox).filter(lambda obj: obj.get("upright", True))
                text = crop.extract_text(x_tolerance=2, y_tolerance=3) or ""
                if text.strip():
                    blocks.append(dict(kind="pdf_column", text=text.strip(), source={**loc, "column": column + 1, "bbox": list(bbox)}))
            flags = ["PDF pilot: native text and reading order need review; not a verified complete recipe."]
            if book_id == "thai-food": flags.append("Existing OCR text may contain recognition errors; check quantities against the page.")
            length = sum(len(b["text"]) for b in blocks)
            audit.append(dict(pdf_page=number, printed_page=printed, text_characters=length, columns=len(blocks),
                              status="extracted_for_review" if length >= 100 else "insufficient_text_or_image_page"))
            if length < 100:
                continue
            heading = "Curries" if book_id == "thai-food" else ("Steaming and submersion" if number < 594 else "Braising and stewing")
            sections.append(dict(id=stable("s_", book_id + str(number)), title=f"{heading} — PDF page {number}",
                                 headings=[title, heading], source=loc, blocks=blocks, flags=flags))
    return sections, audit


def passage_chunks(section, tokenizer):
    """Exact character slices, with section context; no missing text between chunks."""
    spans, parts, cursor = [], [], 0
    for i, block in enumerate(section["blocks"]):
        if not block["text"] or block["kind"] == "image": continue
        text = block["text"]
        if parts: cursor += 2
        spans.append((cursor, cursor + len(text), i))
        parts.append(text); cursor += len(text)
    body = "\n\n".join(parts)
    section["text"] = body
    prefix = " > ".join(section["headings"])
    enc = tokenizer.encode(prefix, add_special_tokens=False)
    if len(enc.ids) > 60: prefix = prefix[:enc.offsets[59][1]]
    offset, passages = 0, []
    while offset < len(body):
        rest = body[offset:]
        tokens = tokenizer.encode(rest, add_special_tokens=False)
        end = len(rest) if len(tokens.ids) <= 360 else tokens.offsets[359][1]
        if end < len(rest):
            boundary = rest.rfind("\n\n", end // 2, end)
            if boundary < 0:
                matches = list(re.finditer(r"[.!?][\s]+", rest[end // 2:end]))
                boundary = end // 2 + matches[-1].end() if matches else -1
            if boundary > 0: end = boundary
        end += offset
        text = body[offset:end]
        touched = [i for start, stop, i in spans if stop > offset and start < end]
        if text.strip() and touched:
            locs = [section["blocks"][i]["source"] for i in touched]
            flags = list(section["flags"])
            if any(section["blocks"][i]["kind"] == "table" for i in touched):
                flags.append("Contains table material; use section blocks for complete rows and headers.")
            passages.append(dict(id=stable("p_", section["id"] + str(offset) + text), section_id=section["id"],
                title=section["title"], headings=section["headings"], text=text, char_start=offset, char_end=end,
                block_indices=touched, source=locs[0], source_spans=locs, flags=flags,
                embedding_text=prefix + "\n" + text))
            # Exact offsets preserve mixed prose/advice boundaries without
            # guessing semantic labels or changing the established chunk IDs.
            passages[-1]["block_spans"] = [dict(
                block_index=i, kind=section["blocks"][i]["kind"],
                passage_char_start=max(start, offset) - offset,
                passage_char_end=min(stop, end) - offset,
                block_char_start=max(start, offset) - start,
                block_char_end=min(stop, end) - start,
            ) for start, stop, i in spans if stop > offset and start < end]
            if "recipe_context" in section:
                passages[-1]["recipe_context"] = section["recipe_context"]
        offset = end
    for i, passage in enumerate(passages):
        passage.update(previous_id=passages[i-1]["id"] if i else None,
                       next_id=passages[i+1]["id"] if i+1 < len(passages) else None)
    return passages


def import_knowledge(source_dir, data_dir):
    existing = data_dir / 'knowledge_sections.jsonl'
    if existing.exists():
        from .importer import BOOKS
        known = {b[0] for b in EPUBS + PDFS} | set(BOOKS)
        if {s['source']['book_id'] for s in load_jsonl(existing)} - known:
            raise ValueError('Pilot knowledge import would discard expanded books. Build a fresh candidate using the documented EPUB batch workflow.')
    from tokenizers import Tokenizer
    model = embedder(data_dir)
    tokenizer = Tokenizer.from_str(model.model.tokenizer.to_str()); tokenizer.no_truncation()
    from .epub_index import capture, save
    sections, audit, book_indexes = [], {}, []
    for book_id, pattern, title, author in EPUBS:
        path = next((source_dir / "EPUB").glob("*" + pattern + "*.epub"))
        book_indexes.append(capture(path, book_id, title, author))
        imported = epub_sections(path, book_id, title, author)
        sections.extend(imported)
        print(f"EPUB {title}: {len(imported)} sections", flush=True)
    for book_id, pattern, title, author, pages in PDFS:
        path = next((source_dir / "PDF").glob("*" + pattern + "*.pdf"))
        imported, report = pdf_sections(path, book_id, title, author, pages)
        sections.extend(imported); audit[book_id] = report
        print(f"PDF {title}: {len(imported)} page sections", flush=True)
    from .content_ingestion import recipe_context_sections, write_classification_report
    recipe_path = data_dir / "recipes.jsonl"
    if recipe_path.exists():
        sections.extend(recipe_context_sections(load_jsonl(recipe_path)))
    passages = [p for s in sections for p in passage_chunks(s, tokenizer)]
    assert all(len(tokenizer.encode(p["embedding_text"]).ids) <= 512 for p in passages)
    data_dir.mkdir(parents=True, exist_ok=True)
    for name, records in [("sections", sections), ("passages", passages)]:
        dest = data_dir / ("knowledge_" + name + ".jsonl")
        tmp = dest.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)); tmp.replace(dest)
    report = dict(version=VERSION, sections=len(sections), passages=len(passages),
                  by_book=dict(Counter(p["source"]["book_id"] for p in passages)), pdf_pilot=audit,
                  image_blocks=sum(b["kind"] == "image" for s in sections for b in s["blocks"]),
                  tables=sum(b["kind"] == "table" for s in sections for b in s["blocks"]))
    report['book_indexes'] = [bundle['report'] for bundle in book_indexes]
    report['content_classification'] = write_classification_report(data_dir, passages)
    (data_dir / "knowledge_import_report.json").write_text(json.dumps(report, indent=2))
    for bundle in book_indexes: save(bundle, data_dir)
    return report


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def build_knowledge_index(data_dir):
    passages = load_jsonl(data_dir / "knowledge_passages.jsonl")
    sections = load_jsonl(data_dir / "knowledge_sections.jsonl")
    from .content_ingestion import classification_report
    # Validate the optional labels before any index mutation. Labels never
    # affect source text, embeddings or unfiltered ranking.
    classification_report(data_dir, passages)
    hashes = [hashlib.sha256((EMBED_MODEL + p["embedding_text"]).encode()).hexdigest() for p in passages]
    cached = {}
    vector_path = data_dir / "knowledge_vectors.npz"
    if vector_path.exists():
        with np.load(vector_path, allow_pickle=False) as old:
            cached = dict(zip(old["hashes"].tolist(), old["vectors"]))
    missing = [(h, p["embedding_text"]) for h, p in zip(hashes, passages) if h not in cached]
    if missing:
        model = embedder(data_dir)
        for start in range(0, len(missing), 96):
            batch = missing[start:start+96]
            for (h, _), vector in zip(batch, model.passage_embed([t for _, t in batch], batch_size=24)):
                cached[h] = vector / max(np.linalg.norm(vector), 1e-12)
            print(f"Passage embeddings {min(start+96, len(missing))}/{len(missing)}", flush=True)
    vectors = np.array([cached[h] for h in hashes], dtype=np.float32)
    tmp = data_dir / "knowledge_vectors.tmp.npz"
    np.savez_compressed(tmp, vectors=vectors, ids=np.array([p["id"] for p in passages]), hashes=np.array(hashes))
    tmp.replace(vector_path)
    dbtmp = data_dir / "knowledge.tmp.sqlite"
    with sqlite3.connect(dbtmp) as db:
        db.executescript("DROP TABLE IF EXISTS passages; DROP TABLE IF EXISTS sections; DROP TABLE IF EXISTS passage_fts; CREATE TABLE passages(id TEXT PRIMARY KEY,record TEXT); CREATE TABLE sections(id TEXT PRIMARY KEY,record TEXT); CREATE VIRTUAL TABLE passage_fts USING fts5(id UNINDEXED,title,body);")
        for p in passages:
            db.execute("INSERT INTO passages VALUES(?,?)", (p["id"], json.dumps({k:v for k,v in p.items() if k != "embedding_text"}, ensure_ascii=False)))
            db.execute("INSERT INTO passage_fts VALUES(?,?,?)", (p["id"], " > ".join(p["headings"]), p["text"]))
        db.executemany("INSERT INTO sections VALUES(?,?)", [(s["id"], json.dumps(s, ensure_ascii=False)) for s in sections])
    dbtmp.replace(data_dir / "knowledge.sqlite")
    report = dict(passages=len(passages), sections=len(sections), embedding_model=EMBED_MODEL,
                  source_fingerprint=hashlib.sha256((data_dir / "knowledge_passages.jsonl").read_bytes()).hexdigest(),
                  books=list({p["source"]["book_id"]: {"id":p["source"]["book_id"],"title":p["source"]["book"],"author":p["source"]["author"],"format":p["source"]["format"]} for p in passages}.values()))
    from .content_ingestion import write_classification_report
    report['content_classification'] = write_classification_report(data_dir, passages)
    (data_dir / "knowledge_index_report.json").write_text(json.dumps(report, indent=2))
    return report


class KnowledgeIndex:
    def __init__(self, data_dir, model):
        self.data_dir, self.model = data_dir, model
        self.report = json.loads((data_dir / "knowledge_index_report.json").read_text())
        with sqlite3.connect(data_dir / "knowledge.sqlite") as db:
            self.passages = {k:json.loads(v) for k,v in db.execute("SELECT id,record FROM passages")}
        from .content_labels import ContentLabels
        self.content_labels = ContentLabels(data_dir, self.passages)
        with np.load(data_dir / "knowledge_vectors.npz", allow_pickle=False) as saved:
            self.vectors, self.ids = saved["vectors"], saved["ids"].tolist()
        ordered = list({p["section_id"]: None for p in self.passages.values()})
        first = {}
        for passage in self.passages.values():
            first.setdefault(passage["section_id"], passage)
        self.adjacent_sections = {}
        for index, sid in enumerate(ordered):
            self.adjacent_sections[sid] = [
                {"id": first[other]["section_id"], "title": first[other]["title"],
                 "first_passage_id": first[other]["id"], "source": first[other]["source"],
                 "direction": direction}
                for j, direction in [(index-1, "previous"), (index+1, "next")]
                if 0 <= j < len(ordered)
                for other in [ordered[j]]
                # Projected recipe notes are appended for discovery, not in
                # whole-book reading order. Their recipe link supplies context.
                if 'recipe_context' not in first[sid] and 'recipe_context' not in first[other]
                if first[other]["source"]["book_id"] == first[sid]["source"]["book_id"]]

    def search(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"query", "limit", "book", "format", "content_labels"}:
            raise ValueError("Expected query, limit, book, format and/or content_labels.")
        from .content_labels import validate_filter
        labels = validate_filter(payload.get("content_labels"))
        query, limit, book, format = (payload.get(k, default) for k, default in [("query", None),("limit",6),("book",None),("format",None)])
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 1500: raise ValueError("query must contain 1–1500 characters.")
        if type(limit) is not int or not 1 <= limit <= 20: raise ValueError("limit must be 1–20.")
        if book is not None and (not isinstance(book,str) or book not in {b["id"] for b in self.report["books"]}): raise ValueError("Unknown knowledge book.")
        if format not in (None,"epub","pdf"): raise ValueError("format must be epub or pdf.")
        eligible = [i for i,rid in enumerate(self.ids) if (not book or self.passages[rid]["source"]["book_id"] == book) and (not format or self.passages[rid]["source"]["format"] == format)]
        label_filter = {"labels": labels, "match": "any", "passages_before_label_filter": len(eligible),
                        "unclassified_passages": sum(self.content_labels.annotations[self.ids[i]]["status"] == "unclassified" for i in eligible),
                        "unassessed_by_label": {label: sum(label in self.content_labels.annotations[self.ids[i]]["unassessed_labels"] for i in eligible) for label in labels}}
        limitations = ["Passages are source evidence, not validated cooking advice. PDF pilot passages carry review flags.",
                       "Content labels are generated inferences, not author metadata; labels can overlap and classification can be incomplete or mistaken. Label filters exclude unclassified passages; omit the filter for broad discovery."]
        if labels:
            eligible = [i for i in eligible if set(labels).intersection(self.content_labels.annotations[self.ids[i]]["labels"])]
        if not eligible: return dict(query=query, results=[], eligible_count=0, content_label_filter=label_filter, limitations=limitations)
        q = np.array(list(self.model.query_embed([query])))[0]; q /= max(np.linalg.norm(q),1e-12)
        ranked = np.argsort(-(self.vectors[eligible] @ q))[:60]
        scores = {self.ids[eligible[i]]: 1/(60+rank) for rank,i in enumerate(ranked,1)}
        terms = sorted(set(t for t in words(query) if t not in STOP))
        if terms:
            with sqlite3.connect(self.data_dir / "knowledge.sqlite") as db:
                matches = db.execute("SELECT id FROM passage_fts WHERE passage_fts MATCH ? ORDER BY bm25(passage_fts,0,3,1)", (" OR ".join('"'+t+'"' for t in terms),)).fetchall()
            allowed = {self.ids[i] for i in eligible}
            for rank,rid in enumerate([r for (r,) in matches if r in allowed][:60],1): scores[rid] = scores.get(rid,0) + 1/(60+rank)
        hits = [{**self.content_labels.decorate(self.passages[rid]), "score": round(scores[rid],6)} for rid in sorted(scores, key=lambda r:(-scores[r],r))[:limit]]
        return dict(query=query,results=hits,eligible_count=len(eligible),content_label_filter=label_filter,limitations=limitations)

    def read(self, passage_id, context=1):
        if not isinstance(passage_id,str) or not re.fullmatch(r"p_[a-f0-9]{16}",passage_id): raise ValueError("Use a passage ID from knowledge search.")
        if type(context) is not int or not 0 <= context <= 2: raise ValueError("context must be 0–2.")
        passage = self.passages[passage_id]
        neighbors = []
        for direction in ("previous_id", "next_id"):
            current = passage
            for _ in range(context):
                if not current[direction]: break
                current = self.passages[current[direction]]; neighbors.append(current)
        with sqlite3.connect(self.data_dir / "knowledge.sqlite") as db:
            section = json.loads(db.execute("SELECT record FROM sections WHERE id=?",(passage["section_id"],)).fetchone()[0])
        # Include full touched tables/figures, even if their text spans several chunks.
        assets = [b for b in section["blocks"] if b["kind"] == "image"]
        tables = [section["blocks"][i] for i in passage["block_indices"] if section["blocks"][i]["kind"] == "table"]
        return dict(passage=self.content_labels.decorate(passage), context=[self.content_labels.decorate(p) for p in sorted(neighbors,key=lambda x:x["char_start"])],
                    section={k:section[k] for k in ("id","title","headings","source","flags")},
                    adjacent_sections=self.adjacent_sections[passage["section_id"]], tables=tables, figures=assets)
