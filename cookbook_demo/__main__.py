"""Small local entry point to the same four-tool service used by MCP."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys

from library_v2.contracts import TOOLS
from library_v2.service import SnapshotService
from .importer import build, profiles


def cursor_secret(directory):
    configured = os.environ.get("COOKBOOK_CURSOR_SECRET")
    if configured:
        return configured
    path = Path(directory) / ".cursor-secret"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text(encoding="utf-8")
    with os.fdopen(fd, "w") as stream:
        value = secrets.token_hex(32)
        stream.write(value)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import and query public-domain cookbook evidence.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sources", help="Show source URLs, hashes and supported scope")
    importer = commands.add_parser("import", help="Build a new immutable library")
    importer.add_argument("--output", type=Path, default=Path("data/demo"))
    importer.add_argument("--cache", type=Path, default=Path("data/downloads"))
    importer.add_argument("--offline", action="store_true")
    call = commands.add_parser("call", help="Call one service tool with JSON arguments")
    call.add_argument("tool", choices=TOOLS)
    call.add_argument("arguments", nargs="?", default="{}")
    call.add_argument("--library", type=Path, default=Path("data/demo"))
    serve = commands.add_parser("serve", help="Start a local MCP server over stdio")
    serve.add_argument("--library", type=Path, default=Path("data/demo"))
    args = parser.parse_args(argv)
    try:
        if args.command == "sources":
            result = profiles()
        elif args.command == "import":
            result = build(args.output, args.cache, args.offline)
        else:
            db = args.library / "library.sqlite"
            if not db.is_file():
                parser.error("Library not found; run cookbook-demo import first.")
            service = SnapshotService(db, cursor_secret(args.library))
            try:
                if args.command == "serve":
                    from .stdio import serve
                    serve(service)
                    return 0
                arguments = json.loads(args.arguments)
                if not isinstance(arguments, dict):
                    parser.error("Tool arguments must be a JSON object.")
                result = getattr(service, args.tool)(**arguments)
            finally:
                service.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if isinstance(result, dict) and result.get("errors") else 0
    except (ValueError, OSError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
