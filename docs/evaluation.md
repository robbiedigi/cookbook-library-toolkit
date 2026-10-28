# What the checks establish

The portfolio separates executable evidence from intended behavior. A schema-valid record is not automatically a faithful extraction, a complete recipe or a useful assistant answer.

## Reproduce

```sh
python -m pip install -c requirements.lock -e '.[server,test]'
python -m unittest discover -s tests -v
python -m unittest discover -s tests/v2 -v
cookbook-demo import
python scripts/verify_demo.py --library data/demo --output data/verification.json
```

The first two commands after installation run synthetic tests without downloading source books. The import requires the two source downloads unless already cached. The final check runs offline and reads every public canonical record through the service.

## Layers of evidence

| Check | Establishes | Does not establish |
|---|---|---|
| Pinned source checksum | The importer received the inspected source bytes | Copyright status in every jurisdiction |
| Adapter fixtures | Boundaries, paragraph text, entity decoding, page handling, failure on unsupported structures | Every real-book extraction is correct |
| Canonical schema and revision checks | Contract compliance and consistent content identity | Culinary correctness or completeness |
| Reader and service regressions | Citation/qualification preservation, pagination, cursor guards, choices, source-range reconstruction and filter semantics on fixtures | Reviewed labels and dependencies for the public books |
| MCP/direct-service parity | All four tools return the same structured evidence through the local adapter | Behavior of every external assistant |
| Full public-demo delivery check | Every canonical recipe/passage survives the serving read with its text, source locator and qualifications | An independent audit against every page of the original books |

## Recorded public run

On October 28, 2026, the pinned sources produced 2 books, 12 selected recipes and 723 knowledge records. Full delivery verification read all **735 records**, compared **1,225 source blocks**, and retained each document locator and qualification code. The database was **5,713,920 bytes**; the largest response in that run was **3,828 bytes**. The report includes elapsed time as local diagnostic data; it is not a latency benchmark or a hosted performance promise.

The snapshot for that run was `40dbaacdb2148000e2a49d5e2e8137566483f9c26e3182a7b651daa3d625661e`. Repeated builds from the same pinned inputs must have the same logical snapshot and canonical JSON. Changing the source profile or compiler can legitimately change that identity. SQLite binary bytes are not the cross-platform identity contract.

Local validation used macOS on Apple Silicon with Python 3.14. The initial core-only export passed 48 tests; the expanded library/importer toolkit passes **134 tests with no skips** (84 importer/toolkit tests and 50 v2 tests). A fresh environment built and installed the project as a wheel, then downloaded both sources into an empty cache and reproduced the same snapshot. A real MCP stdio subprocess initialized successfully and exercised all four tools through a client session. These are local installation and protocol checks, separate from a hosted deployment or an assistant answer-quality evaluation.

Synthetic tests cover required and optional ingredient occurrence distinctions, derivative/homonym matching, source-bound relationship revisions, dependency choices, cycles, partial failures, immutable reads and oversize delivery. They use explicitly invented examples. Two private-corpus-only test classes, one test coupled to a private batch-audit runner and the private full-corpus evaluation runner are omitted from the public distribution.

The CI matrix is configured for Linux/Python 3.10, 3.12 and 3.14. Local validation is recorded separately; this file does not claim that CI has run in a new public repository or that a hosted service has been deployed.

## Toolkit validation

The expanded export was built as version 0.2.0 and installed as a wheel in a fresh temporary environment outside the repository. Installed commands built a personal Markdown snapshot, extracted the synthetic EPUB (one candidate and one held incomplete record), replayed retained PDF evidence through review/assembly/projection, and created a separate hybrid projection. The PDF tests retain original OCR, distinguish file page 1 from printed label 7, reject changed page-render evidence and prevent recursive output placement.

A real local `BAAI/bge-small-en-v1.5` download, offline index and offline query completed on the public demo: **12 recipes, 24 vectors, 384 dimensions, zero generated profiles**. Three returned IDs were read successfully through the unchanged v2 service. This used FastEmbed 0.7.4, ONNX Runtime 1.31.1 and NumPy 2.5.4 on macOS/Apple Silicon, Python 3.14. The embedding cache's upstream model revision is not pinned by the package; keep the cache with an experiment when comparing exact vector results.

The vector reuse test uses deterministic stub embeddings to verify cache invalidation and equality to a full rebuild. That is distinct from the real-model smoke test. Neither establishes semantic-search relevance gains, retrieval latency or correctness of historical advice. Complete public-demo delivery still compares all 735 records and 1,225 source blocks successfully.

The public PDF example is explicitly synthetic Docling-shaped evidence, not a real PDF/OCR run. Live Docling/Apple Vision extraction was not rerun for this release. Review-contract tests establish retention and failure behavior, not OCR accuracy or universal layout coverage. EPUB/PDF candidates have not been automatically promoted into the public v2 demo.

## Further evaluation worth doing

- Independently compare a stratified sample against original pages: quantities, omissions, paragraph boundaries and source locations.
- Create a reviewed ingredient/dependency dataset for a small source slice before claiming those capabilities for the public books.
- Freeze a set of cooking questions and judge both retrieval and complete assistant answers against source evidence. Report failures and qualifications as well as passes.
- Measure relevant-candidate recall, bytes read and repeated tool calls independently. Smaller responses are useful only if the necessary evidence survives.
- Compare semantic retrieval against the lexical baseline on the same judged inputs before integrating semantic ranking into MCP or claiming an advantage.
