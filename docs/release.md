# Public distribution boundary

This project is distributed as a fresh source tree. It contains the reusable v2 runtime, the separate hybrid retrieval engine, personal/EPUB/PDF ingestion and review tools, a public-domain demonstration importer, synthetic examples/tests and documentation written for external readers.

It intentionally excludes original private repository history, the commercial cookbook corpus, personal recipes, deployment records, credentials, account identifiers and historical evaluation archives. Removing files in a later commit would not remove them from an existing Git history. Publish this exported tree in a **new repository**, rather than changing the original repository’s visibility or pushing its branches/tags.

`EXPORT-MANIFEST.json` records the base implementation commit and SHA-256 of each exported file. The base commit is background provenance; the manifest hashes describe the actual working-tree export. Runtime files are copied directly, including the shared source chunker. Portable tests are copied from the maintained suite; the exporter removes two private-only test classes, one test coupled to the private batch-audit runner, and the private full-corpus evaluation entry point. The synthetic canonical fixture is labeled as such.

Downloads, generated canonical text and SQLite snapshots belong under ignored `data/`. The source code ZIP contains no source books or private recipe collections. It includes invented examples and short regression snippets. Users reproduce the demo by downloading the declared public sources. Refer to [NOTICE](../NOTICE.md) before redistributing downloaded eBooks or derived text.

Before publishing a changed export, run the two unit suites and the public-demo verification in an isolated checkout. Inspect the exact source tree being published. The export manifest and an allowlist make accidental inclusion less likely, but they are not a universal secret-detection guarantee.

The default software license is MIT. Third-party libraries retain their own licenses; downloaded cookbook text has its own status and notices. The repository name, destination and visibility should be chosen when publishing. No hosting service or remote account is needed for local use.
