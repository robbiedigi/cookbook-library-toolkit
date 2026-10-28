import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Personal/EPUB/photo-PDF ingestion and optional hybrid search")
    commands = parser.add_subparsers(dest="command", required=True)
    personal = commands.add_parser("personal", help="Compile an editable Markdown recipe collection into v2")
    personal.add_argument("--collection", type=Path, required=True)
    personal.add_argument("--previous", type=Path)
    personal.add_argument("--output", type=Path, required=True)
    epub = commands.add_parser("epub", help="Extract an edition using an explicit layout profile")
    epub.add_argument("--source", type=Path, required=True)
    epub.add_argument("--profile", type=Path, required=True)
    epub.add_argument("--output", type=Path, required=True)
    review = commands.add_parser("pdf-review", help="Turn retained OCR evidence into a review packet")
    review.add_argument("--extraction", type=Path, required=True)
    review.add_argument("--profile", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    project = commands.add_parser("pdf-project", help="Project a source-reviewed PDF assembly into candidate records")
    project.add_argument("--bundle", type=Path, required=True)
    project.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("hybrid-prepare", help="Make a separate search sidecar for an existing v2 library")
    prepare.add_argument("--library", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    for name in ("download-embeddings", "index", "search"):
        command = commands.add_parser(name)
        command.add_argument("--data", type=Path, required=True)
        if name == "index":
            command.add_argument("--reuse-from", type=Path)
        if name == "search":
            command.add_argument("query")
            command.add_argument("--limit", type=int, default=6)
    args = parser.parse_args(argv)
    try:
        from . import pipelines
        if args.command == "personal":
            from library_v2.personal import build
            manifest = build(args.collection, args.output, args.previous)
            result = dict(recipes=len(manifest["inputs"]), compiled=manifest["compiled"], delta=manifest["delta"],
                          acceptance=manifest["acceptance"])
        elif args.command == "epub":
            result = pipelines.import_epub(args.source, args.profile, args.output)
        elif args.command == "pdf-review":
            result = pipelines.review_pdf(args.extraction, args.profile, args.output)
        elif args.command == "pdf-project":
            result = pipelines.project_pdf(args.bundle, args.output)
        elif args.command == "hybrid-prepare":
            result = pipelines.prepare_hybrid(args.library, args.output)
        elif args.command == "download-embeddings":
            result = pipelines.download_embeddings(args.data)
        elif args.command == "index":
            from cookbook.search import build_index
            result = build_index(args.data.resolve(), reuse_from=args.reuse_from)
        else:
            from cookbook.search import SearchEngine
            if not 1 <= args.limit <= 50:
                raise ValueError("limit must be between 1 and 50")
            result = SearchEngine(args.data.resolve()).search(args.query, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, ImportError) as exc:
        print(f"{exc}\nSee docs/toolkit.md for required extras, input formats and review steps.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
