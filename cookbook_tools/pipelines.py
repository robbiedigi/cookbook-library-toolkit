"""Thin adapters around existing import/review/search code; no publication steps."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_rows(path, rows):
    Path(path).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


@contextmanager
def fresh_output(output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Choose a new output directory: " + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".cookbook-tool-", dir=output.parent) as temporary:
        stage = Path(temporary) / "candidate"
        stage.mkdir()
        yield stage
        stage.rename(output)


def import_epub(source, profile_path, output):
    from cookbook.epub_batch import extract
    from cookbook.import_review import pending_import_review
    source = Path(source).resolve()
    profile = json.loads(Path(profile_path).read_text())
    if sha(source) != profile["sha256"]:
        raise ValueError("EPUB bytes differ from the edition profile")
    book = dict(id=profile["id"], title=profile["title"], author=profile["author"],
                files=[dict(name=source.name, format="epub")])
    layout = dict(profile["layout"], restrict_members=True)
    records, held, report = extract(book, source.parent, profile=layout,
                                    member_pattern=profile["member_pattern"])
    # The reusable extractor records absolute paths for local audit. This new
    # portable candidate retains the source itself under a relative path.
    for record in records + held:
        record["source"]["file"] = "sources/" + source.name
    sections = report.pop("supporting_sections")
    report["supporting_section_count"] = len(sections)
    report["dependency_review"] = pending_import_review(records + held, {book["id"]: profile["sha256"]})
    report["status"] = "extracted_candidates_not_source_accepted"
    with fresh_output(output) as stage:
        (stage / "sources").mkdir()
        shutil.copyfile(source, stage / "sources" / source.name)
        write_rows(stage / "recipes.jsonl", records)
        write_rows(stage / "held-recipes.jsonl", held)
        write_rows(stage / "knowledge-sections.jsonl", sections)
        write_json(stage / "profile.json", profile)
        write_json(stage / "import-report.json", report)
    return {k: v for k, v in report.items() if k not in ("held", "toc_matched", "toc_unresolved", "dependency_review")}


def review_pdf(extraction, profile_path, output):
    from cookbook.pdf_assembly import load_extraction
    from cookbook.pdf_batch_review import page_view, partition, unit_warnings
    profile = json.loads(Path(profile_path).read_text())
    manifest, blocks, pages, reports = load_extraction(Path(extraction))
    if profile["sha256"] != manifest["sha256"]:
        raise ValueError("PDF review profile belongs to another source")
    if profile["book_id"] != manifest["book_id"]:
        raise ValueError("PDF review book identity differs")
    evidence = []
    for number, page in sorted(pages.items()):
        row = page_view(page)
        # 'review_lines' is the assembler's input field, not an acceptance label.
        row.update(raw_page=page, review_lines=row["lines"], review_status="unreviewed")
        evidence.append(row)
    sections, excluded = partition(blocks, profile, pages)
    warnings = [dict(pdf_page=row["pdf_page"], cell_index=line["cell_index"], warning=warning)
                for row in evidence for line in row["lines"] for warning in unit_warnings(line["text"])]
    report = dict(book_id=manifest["book_id"], source_sha256=manifest["sha256"], pages=len(pages),
                  blocks=len(blocks), sections=len(sections), excluded_blocks=excluded,
                  unit_warnings=warnings, status="review_required", recipes_promoted=0)
    with fresh_output(output) as stage:
        write_rows(stage / "raw-blocks.jsonl", blocks)
        write_rows(stage / "page-evidence.jsonl", evidence)
        write_rows(stage / "section-candidates.jsonl", sections)
        write_json(stage / "book-index.json", dict(source=manifest, entries=[], status="not_captured"))
        write_json(stage / "report.json", report)
        write_json(stage / "review-ledger.template.json", dict(source_sha256=manifest["sha256"],
            pages={}, recipes=[], corrections=[], questions=[], non_recipe_cells=[]))
    return report


def project_pdf(bundle, output):
    from cookbook.pdf_runtime import project, context_sections
    from cookbook.import_review import pending_import_review
    bundle = Path(bundle)
    if Path(output).resolve().is_relative_to(bundle.resolve()):
        raise ValueError("Choose an output outside the reviewed bundle")
    source = json.loads((bundle / "book-index.json").read_text())["source"]
    assembled = read_rows(bundle / "recipes.jsonl")
    contexts = json.loads((bundle / "recipe-contexts.json").read_text())
    pages = read_rows(bundle / "page-evidence.jsonl")
    records = project(assembled, contexts, pages, source)
    sections = context_sections(contexts, source)
    review = pending_import_review(records, {source["book_id"]: source["sha256"]})
    with fresh_output(output) as stage:
        write_rows(stage / "recipes.jsonl", records)
        write_rows(stage / "knowledge-sections.jsonl", sections)
        # Retain the actual ledger and raw pages, not just a path to a mutable folder.
        shutil.copytree(bundle, stage / "review-evidence")
        write_json(stage / "import-review.json", review)
    return dict(recipes=len(records), knowledge_sections=len(sections), status="candidate_only",
                dependency_coverage="unreviewed", personally_tested=None)


def prepare_hybrid(library, output):
    """Create an independent legacy-format search sidecar for a v2 snapshot.

    Keep original IDs for authoritative v2 reads. This does not change MCP
    ranking, rewrite a source, accept metadata, or create dependency reviews.
    """
    from library_v2.store import Snapshot, all_blocks, block_text
    snapshot = Snapshot(Path(library) / "library.sqlite")
    try:
        rows = []
        for row in snapshot.db.execute("SELECT content_json FROM recipes ORDER BY id"):
            record = json.loads(row[0])
            alias_row = snapshot.db.execute("SELECT aliases FROM recipe_fts WHERE recipe_id=?", (record["id"],)).fetchone()
            aliases = alias_row[0].splitlines() if alias_row and alias_row[0] else []
            citations = record["citations"]
            cite = citations[0]
            rows.append(dict(id=record["id"], book_id=record["edition_id"], book=cite["book"],
                author=cite["author"], title=record["title"], aliases=aliases,
                ingredients=[block_text(b) for group in record["ingredient_groups"] for b in group["blocks"]],
                instructions=[block_text(b) for b in record["method_blocks"]],
                headnote="\n".join(block_text(b) for b in record["headnotes"]),
                source_text="\n".join(block_text(b) for b in all_blocks(record)), blocks=[],
                warnings=[q["message"] for q in record["uncertainties"]],
                source=dict(format=cite["format"], sha256=(cite.get("document") or {}).get("sha256"),
                    member=cite["epub_member"], anchor=cite["epub_anchor"],
                    printed_page=cite["printed_labels"][0] if cite["printed_labels"] else None,
                    printed_pages=cite["printed_labels"], pdf_pages=cite["pdf_file_pages"],
                    snapshot=snapshot.id, citations=citations), **{"yield": record["yield"]["text"]}))
            if not rows[-1]["ingredients"]:
                rows[-1]["source_prose_excerpt"] = rows[-1]["source_text"]
        if not rows:
            raise ValueError("Snapshot has no recipes to index")
        report = dict(recipes=len(rows), source_snapshot=snapshot.id, status="search_sidecar_only",
                      authoritative_reads="Use the unchanged IDs with library_v2.get_recipes")
        with fresh_output(output) as stage:
            write_rows(stage / "recipes.jsonl", rows)
            write_json(stage / "projection.json", report)
        return report
    finally:
        snapshot.close()


def download_embeddings(data):
    import os
    # Only this explicit command enables network model acquisition.
    os.environ["HF_HUB_OFFLINE"] = "0"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from fastembed import TextEmbedding
    from cookbook.search import EMBED_MODEL
    cache = Path(data).resolve().parent / ".cache/fastembed"
    TextEmbedding(EMBED_MODEL, cache_dir=str(cache), threads=2)
    return dict(model=EMBED_MODEL, cache=str(cache), recipe_data_uploaded=False)
