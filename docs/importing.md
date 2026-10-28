# Importing public sources

This page describes the Gutenberg HTML demo. For personal Markdown, EPUB, photographed PDFs and optional embeddings, use the [toolkit guide](toolkit.md). The [new-book review checklist](new-book-import.md) applies across adapters.

## Included profiles

`cookbook_demo/sources.json` records two exact HTML downloads. Both catalog entries state public-domain status in the USA; the relevant source links and credits are in [NOTICE](../NOTICE.md).

| Book | Imported passage scope | Recipe sample |
|---|---|---|
| The National Cook Book (1856) | SOUPS through PRESERVES; stop before SICK | Beef soup, veal soup, green corn soup, pea soup, succotash, noodles for soup |
| The New England Cook Book (1836) | PRACTICAL COOKERY; stop before miscellaneous household receipts | Roast beef, beef steak, chicken pie, chicken salad, apple sauce, tomato catsup |

These scopes produce 723 passages and 12 selected recipe records. A passage may describe a dish or general guidance. Only the explicitly selected headings also become recipe records. Classifications remain unknown. The recipes retain complete normalized prose for their bounded sections, not separate inferred ingredient lists.

## Reproduce an import

```sh
cookbook-demo import --output data/demo --cache data/downloads
cookbook-demo import --offline --output data/rebuild --cache data/downloads
python scripts/verify_demo.py --library data/rebuild
```

The importer writes originals under `sources/`, ordered extraction records to `extraction.jsonl`, canonical records to `books.jsonl`, `recipes.jsonl` and `knowledge.jsonl`, and the serving snapshot to `library.sqlite`. `build-report.json` captures counts and snapshot identity. `sources.json` freezes the catalog used for the build.

Normalization decodes HTML entities, joins layout whitespace, and removes page-number decorations from prose while retaining page labels separately. A source span refers to a normalized paragraph and its DOM path, with codepoint offsets and a hash of that text. These offsets are **not byte offsets into HTML**. Original HTML is the audit source.

## Add or update an edition

1. Establish a source you can use and redistribute as intended. Record its author, edition, catalog URL and rights statement; a publicly readable URL alone is insufficient.
2. Download and retain the complete original, including notices. Inspect representative sections and chapter boundaries. Record its SHA-256. A checksum change is a review event, not a transient error to ignore.
3. Add a profile only if the HTML matches this adapter: direct body-level H2/H3 boundaries and text in P/DIV blocks. Tables, images or another structure need an explicit adapter extension and fidelity tests. The current adapter raises on unsupported elements inside scope.
4. Choose explicit start/stop headings and a recipe sample. Avoid silently turning household advice, medicine, author narrative or chapter introductions into recipes. Duplicate selected headings fail rather than selecting one arbitrarily.
5. Build into a new directory. Compare quantities, fractions, paragraph order, cross-references and source anchors. Check passages near boundaries and retain meaningful source limitations.
6. Run unit tests and full delivery verification. Update expected counts and the evaluation document for the new profile. Store any reviewed metadata separately from source text and bind it to the current revisions.

Updating a source hash changes the edition ID and snapshot. Old cursors must be restarted. Do not replace an active snapshot in place.

The supplied adapter does not download linked images, interpret SVG or transcribe figures. Unsupported in-scope image/table content is held instead of silently omitted. The complete original files are retained even when the indexed scope excludes front matter, indexes, medical sections or household material.

## Review beyond extraction

Structured ingredients require evidence for their exact text spans, optionality and variant scope. Required components need review against the recipe and its surroundings. A mention of another recipe can be optional, contextual or an alternative. Source-based review does not establish personal cooking experience.

No language model is used by this demo import. An assisted review workflow could be added later, but inferred fields must remain explicit, reproducible and distinguishable from the author’s words.
