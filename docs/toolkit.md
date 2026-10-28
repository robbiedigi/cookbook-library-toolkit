# Importers and local search toolkit

The repository has two reusable parts: the evidence library and its ingestion tools. You can use the importers to prepare your own local collection, use hybrid search independently, or compile supported canonical inputs into the four-tool MCP service.

## What connects to what

| Input or component | Output | Next step |
|---|---|---|
| Registered public-domain HTML | Canonical recipes/passages and v2 SQLite snapshot | CLI or four-tool MCP |
| Personal Markdown collection | Canonical recipes and v2 SQLite snapshot | CLI or four-tool MCP |
| EPUB with an explicit edition/layout profile | Structured candidate recipes, held records, supporting sections, original EPUB | Source review; optional hybrid indexing |
| Photographed/scanned PDF | Retained OCR and layout evidence | Review packet, source-bound ledger, reviewed assembly, candidate projection |
| Legacy-shaped candidate `recipes.jsonl` | BM25 and dense-vector indexes | Separate hybrid search CLI |
| Existing v2 snapshot | Independent recipe search projection with unchanged IDs | Hybrid discovery, then authoritative reads from the original snapshot |

**The v2 MCP service uses lexical retrieval.** The included legacy hybrid engine combines SQLite BM25 with two local embedding representations. Running `index` does not enable semantic ranking in MCP. EPUB/PDF candidates are not automatically migrated into v2 or source-accepted: the canonical contracts and lower-level migration code are available, but connecting a new edition requires an explicit reviewed mapping. This is a current integration boundary, not a completed one-click path.

## Install

Use the README's core setup, then add only what you need:

```sh
python -m pip install -c requirements.lock -e '.[authoring]'
python -m pip install -e '.[hybrid]'
```

`authoring` adds markdown-it-py and PyYAML. EPUB extraction needs only the core lxml dependency. `hybrid` adds FastEmbed, ONNX Runtime and NumPy through the declared dependencies. The optional `pdf` extra provides pdfplumber for the lower-level native-text adapter; it does not install the photographed-PDF OCR stack. Install that stack separately as described below. `requirements.lock` constrains the core/server environment; the hybrid extra has its own declared version bounds rather than a cross-platform frozen transitive environment.

## Personal recipes

The editable collection is ordinary Markdown, with one file per recipe and a small collection manifest. Try the invented example:

```sh
cookbook-tools personal --collection examples/personal --output data/personal
cookbook-demo call --library data/personal search_recipes '{"query":"rice"}'
cookbook-demo serve --library data/personal
```

Keep the collection ID and each recipe's front-matter ID stable when renaming files or changing titles. Use H2 sections `Ingredients`, `Method`, `Headnote`, `Notes`, `Source` and `Review notes`; H3 can group ingredients or method stages. Required ingredients and method text must be present. Missing durations or unclear quantities belong in `Review notes`; the builder retains these as qualifications rather than inventing values.

Build each revision into a new directory:

```sh
cookbook-tools personal --collection examples/personal \
  --previous data/personal --output data/personal-next
```

The manifest reports added, edited, renamed and removed records; output retains the Markdown, hashes, canonical JSON and serving database. Archived source documents can be bound by path/hash through `archives` and `origins` in `collection.json`. `scripts/migrate_personal_doc.py` is also included for offline migration of the supported Google Docs table-export layout; use `--help` and inspect its output. It does not fetch a Google document, and it is not a universal document converter. Importing a personal recipe does not establish that it was cooked or tested.

## EPUB: inspect a layout, bind an edition, extract candidates

Generate the synthetic examples once:

```sh
python scripts/make_toolkit_examples.py --output data/toolkit-examples
cookbook-tools epub --source data/toolkit-examples/example.epub \
  --profile data/toolkit-examples/epub-profile.json --output data/epub-candidate
```

The example produces one complete-shaped candidate and one held recipe lacking a method. It is invented test content, not a recipe recommendation. For your own EPUB, supply a profile of this shape:

```json
{
  "id": "my-edition",
  "title": "My cookbook",
  "author": "Author name",
  "sha256": "SHA256_OF_THE_EPUB_BYTES",
  "member_pattern": "OPS/chapter[0-9]+\\.xhtml",
  "layout": {
    "titles": "recipe-title",
    "ingredients": "ingredient",
    "instructions": "method",
    "yields": "yield",
    "groups": "ingredient-group",
    "headnotes": "headnote",
    "barriers": "chapter-title"
  }
}
```

Class lists are space-separated CSS class names, not arbitrary selectors. `member_pattern` is a full-match regular expression over EPUB member paths. Inspect the ZIP's OPF spine and XHTML before selecting these values. Existing edition-specific profiles in `cookbook/epub_batch*.py` demonstrate more complex cases: split titles, tables, fractions, fixed layout, notes and variant boundaries. Their presence is not a claim that an arbitrary edition will parse correctly.

The importer retains the EPUB under `sources/`, source blocks with member/anchor/DOM locations, normalized ingredients and instructions, supporting prose, extraction warnings and the exact profile. `held-recipes.jsonl` keeps failed structural candidates; `import-report.json` starts dependency review as pending. Normalization and source text are separate. Read both candidate and held outputs, account for unmatched content, and compare representative recipes against the original before relying on the extraction. A hash mismatch requires a new inspected edition profile.

## Photographed PDFs: extraction and review are separate stages

The shipped raw extractor uses **Docling 2.132.0, PDFium and Apple Vision OCR on macOS**. It records source hashes, all file pages, OCR cells, geometry and per-chunk output hashes; interrupted extraction can resume only with matching configuration and intact cached evidence. No remote OCR service is enabled. This runner has not been revalidated on a real scanned book as part of the public export. Changing to another OCR engine or platform requires an adapter change and source-fidelity checks.

In a separate compatible Python environment on macOS:

```sh
python -m pip install 'docling==2.132.0' 'ocrmac==1.0.1'
docling-tools models download --output-dir .cache/pdf-parsers/docling
```

Model acquisition is an explicit network step; extraction then runs offline. See the upstream [model download documentation](https://docling-project.github.io/docling/usage/advanced_options/) and [OCR installation options](https://docling-project.github.io/docling/getting_started/installation/). Keep a freeze of the successful OCR environment with your extraction evidence; the lightweight core lock does not constrain Docling's transitive dependencies.

Create a `data/pdf-sources.json` manifest with your actual hash and filename:

```json
{
  "my-scan": {
    "file": "my-scan.pdf",
    "sha256": "SHA256_OF_THE_PDF_BYTES",
    "title": "My scanned cookbook",
    "author": "Author name",
    "force_ocr": true
  }
}
```

Then extract every page:

```sh
python scripts/extract_pdf_book.py --book my-scan \
  --profile-manifest data/pdf-sources.json --sources /absolute/path/to/pdfs \
  --models .cache/pdf-parsers/docling --output data/scan-extraction
```

Always pass `--book`, `--profile-manifest` and `--sources`; historical internal defaults are not included as source data. A review profile is a separate JSON object containing `book_id`, the same `sha256`, and `body: [FIRST_FILE_PAGE, LAST_FILE_PAGE]`, using inclusive, one-based file pages. Create a packet with:

```sh
cookbook-tools pdf-review --extraction data/scan-extraction \
  --profile data/pdf-review-profile.json --output data/scan-review
```

The packet retains every raw block and OCR cell, provisional section boundaries, exclusions and unit-disagreement warnings. It starts with an **empty** review ledger. To populate it, visually inspect original page renders, record each render's path and SHA-256, select title/ingredient/method ranges by their original zero-based `cell_index`, and record printed labels separately from PDF file pages. Ranges are inclusive. The generated example ledger below shows the exact shape. Every selected recipe-page line must be assigned, retained as context or explicitly excluded with a reason.

Corrections require exact `before` and `after` text anchored to a page/cell. Original OCR remains retained. Owner interpretations use a separate layer, with a reason; they must not masquerade as transcription corrections. Unresolved recipe/component questions prevent runtime projection. Source review does not mark a recipe verified complete or personally tested.

```sh
python scripts/assemble_reviewed_pdf.py --source-bundle data/scan-review \
  --ledger data/my-reviewed-ledger.json --output data/scan-assembled
cookbook-tools pdf-project --bundle data/scan-assembled --output data/scan-candidate
```

Retain original PDFs, page renders and all extraction evidence with your local collection. The candidate copies the assembled review bundle; its render references do not automatically copy the original PDFs or render files.

### Replay the PDF review contract without OCR

The example generator creates invented Docling-shaped JSON, OCR cells and an SVG. **It does not generate or scan a PDF.** Use it to learn the review/assembly interface on any supported core platform:

```sh
cookbook-tools pdf-review --extraction data/toolkit-examples/ocr-evidence \
  --profile data/toolkit-examples/pdf-profile.json --output data/pdf-review
python scripts/assemble_reviewed_pdf.py --source-bundle data/pdf-review \
  --ledger data/toolkit-examples/review-ledger.json --output data/pdf-assembled
cookbook-tools pdf-project --bundle data/pdf-assembled --output data/pdf-candidate
```

This yields one invented recipe, with file page `1` and printed label `7`, retained raw evidence and a source-bound review ledger. Tests also change the render bytes and confirm assembly fails.

## Optional local embeddings

The hybrid engine builds two representations per recipe: source-content text and a profile representation. When no reviewed/generated profile sidecar exists, the profile text is derived from existing recipe fields; it is not a new LLM inference. Query ranking combines BM25, dense similarity and the legacy engine's ingredient/title signals. FastEmbed runs `BAAI/bge-small-en-v1.5` locally through ONNX Runtime. No vector database service or API key is needed.

For the public demo, first create an independent projection:

```sh
cookbook-tools hybrid-prepare --library data/demo --output data/hybrid-demo
cookbook-tools download-embeddings --data data/hybrid-demo
cookbook-tools index --data data/hybrid-demo
cookbook-tools search --data data/hybrid-demo 'a warming vegetable soup' --limit 3
```

For EPUB or reviewed PDF candidates, `recipes.jsonl` is already in the engine's input format: substitute `data/epub-candidate` or `data/pdf-candidate` in the final three commands. Held records are not indexed. This CLI indexes recipes; it does not automatically index `knowledge-sections.jsonl` or expand reviewed recipe dependencies.

The explicit download command caches models at `DATA_PARENT/.cache/fastembed`. Indexing and search use that cache offline and do not upload recipe text. Indexing writes `vectors.npz`, `search.sqlite` and a report alongside the candidate JSONL. `--reuse-from OTHER_DATA` reuses vectors only when model and content/profile fingerprints match. Keep each indexed build intact; changing its JSONL makes its index stale. Rebuild into a copied, fresh candidate directory after edits.

`hybrid-prepare` keeps recipe IDs and source snapshot identity, so you can use a returned ID with the original library's `get_recipes`. It projects serving text, not the full canonical archive or dependency graph. It does not infer missing ingredients in the historical demo. The hybrid engine's legacy filtering semantics differ from the current v2 contract; use the current service for qualified reads. No semantic-search quality advantage is claimed without a judged comparison.

## Where the reusable code lives

| Module | Responsibility |
|---|---|
| `library_v2/personal.py` | Markdown authoring, immutable builds and change reports |
| `cookbook/epub_batch.py`, `epub_index.py` | Edition layouts, source-block extraction and EPUB navigation/index evidence |
| `cookbook/pdf_*` | Retained OCR evidence, provisional boundaries, source corrections, reviewed assembly and runtime projection |
| `cookbook/search.py` | BM25 + dense search, fingerprints and incremental vector reuse |
| `cookbook_tools/` | Portable CLI adapters with explicit inputs and fresh candidate outputs |
| `library_v2/` | Canonical validation, compilation, lexical retrieval, complete reads and transports |

Some lower-level modules retain historical edition-specific defaults and optional inference hooks for compatibility. The documented commands are the portable entry points. Local LLM enrichment/reasoning is not installed or invoked by these workflows. Keep all generated inference distinguishable from source evidence if extending them.
