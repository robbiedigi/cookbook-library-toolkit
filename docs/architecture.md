# Architecture and tradeoffs

## Product boundary

The library supplies evidence to an existing cooking assistant. The assistant owns conversational context, meal composition and proposed adaptations. The library owns stable record identity, source fidelity, search/read behavior and explicit qualifications. Keeping those responsibilities separate makes the same corpus usable from a Python process, a command-line tool or an MCP client.

## Storage layers

| Layer | Contents | Lifecycle |
|---|---|---|
| Source | HTML, Markdown, EPUB or PDF and applicable license/credits | Retain exact bytes, identify by SHA-256 |
| Extraction | Ordered blocks, DOM paths or OCR cells/geometry and printed labels | Rebuild from a pinned source with a named parser policy |
| Canonical | Validated books, recipes, passages, citations and uncertainty | Revisions change when record content changes |
| Serving | SQLite tables, FTS indexes, delivery parts and snapshot metadata | Compile into a new file; open read-only |
| Response | Compact cards or complete source reads | Bound by request, snapshot, cursor and serialized byte budget |

Canonical JSONL is inspectable and suitable for a review diff. SQLite is a disposable serving projection, rather than the only copy of the source. The compiler refuses stale revisions, duplicate identities, dangling citations and invalid accepted dependency bindings. Its temporary build is removed on failure.

## Retrieval

Recipe retrieval combines exact normalized-title priority, optional preference terms and SQLite BM25 ranking. Query text is tokenized into quoted terms before entering FTS grammar. Hard filters constrain records independently of ranking. Empty-query enumeration provides explicit totals; ranked search deliberately does not advertise an exhaustive relevant-result count.

Knowledge is searched in bounded, source-bound chunks. Hits resolve to coherent passages for reading. Chunk boundaries are not automatically recipe boundaries, and retrieving one chunk does not establish that the rest of a source record has been read.

The current v2 service keeps deployment simple and avoids model startup, model caches and a separate vector service. The tradeoff is lexical recall: an unmentioned synonym or an implicit cooking concept may not match.

The optional `cookbook/search.py` engine offers a separate BM25 + dense search path. It embeds content and profile representations locally with FastEmbed's BGE-small model, stores vectors in NumPy and lexical fields in SQLite, and reuses vectors only when fingerprints match. A v2 projection retains recipe IDs and snapshot identity for subsequent authoritative reads. This engine retains legacy ranking/filter semantics; it does not replace the v2 service, its dependency expansion or its delivery contract. Adopting its ranking in MCP would require integration and a judged comparison.

## Ingestion boundaries

Personal Markdown and registered public HTML currently compile directly into v2. EPUB layout profiles extract structured candidates and preserve supporting prose. The PDF path retains raw OCR/cells, derives provisional sections, applies a hash-bound review ledger and projects reviewed selections into legacy-shaped candidates. The latter two can feed the standalone hybrid engine; promotion into v2 requires a reviewed canonical mapping. This explicit boundary prevents parser output from being mistaken for accepted source coverage. See [all runnable workflows](toolkit.md).

## Ingredient identity and dependency graph

For reviewed structured inputs, ingredient matching distinguishes a food from its derivatives and homonyms: apple is not apple cider vinegar; oyster is not oyster mushroom. Optional/variant presence and required presence have different semantics. The matcher is a bounded policy, not a universal ontology or allergy certification.

Dependency relationships bind source/target record revisions and carry review status, disposition and supporting evidence. Required nodes can expand automatically; alternatives retain choice groups. The service bounds traversal by nodes, edges and depth, detects cycles and exposes unresolved source issues. A changed source cannot silently inherit an accepted old relationship.

The public HTML adapter creates neither ingredient assertions nor accepted relationships. Both capabilities remain available in the runtime and have synthetic regressions. Importing text is intentionally a different claim from reviewing it.

## Delivery and client obligations

The four tool contracts validate input locally and reject unknown fields. Output has a snapshot identity, requested-root receipts, errors, record parts and an explicit continuation. A response can contain successful roots alongside a failed one.

The default JSON envelope is 128 KiB. Small records arrive whole. Large records arrive as a header followed by ordered text or table ranges. Unicode codepoint offsets and original table row indices allow exact reconstruction. Clients must keep arguments unchanged while following `next_cursor`, inspect both top-level errors and root status, and treat `complete` as delivery completeness only.

Cursors use HMAC and include a digest of the effective input, tool, position and snapshot. They cannot be reused for changed filters or a different database. Local CLI invocations share a generated file secret in the library directory; deleting that secret invalidates old cursors. Remote deployment should provide a stable random secret through its environment.

## Serving and authentication

Local stdio is a child process owned by the client, with no network listener. Its registered schemas and dispatcher reuse the core service, and structured JSON is delivered once with a small textual receipt. This demo adapter serves text; it does not load image attachments.

`library_v2.transport` is the optional hosted ASGI/MCP adapter. It verifies RS256 JWT issuer, audience, expiry, scope and exact subject allowlist against configured JWKS. It supports read endpoints at `/v2/{tool}`, MCP at `/mcp`, and health at `/healthz`. Run with `python -m library_v2.transport` after setting the variables documented in `.env.example`; the file is not automatically loaded. Hosting, TLS and identity-provider provisioning are your responsibility.

The hosted transport uses a bounded worker pool and per-thread read-only database connections. Metadata-only activity logging avoids raw queries, response bodies and credentials. Original-image delivery is a hosted capability when its separately configured assets are present, not a feature of these text-only sources.

## Extension boundary

An EPUB or PDF adapter should emit the same canonical contract, retain original assets and support source review. File-page indices must remain distinct from printed page labels. OCR corrections, author text, user interpretations and generated inference must have different provenance. Do not make a source look complete merely because a parser produced valid JSON.
