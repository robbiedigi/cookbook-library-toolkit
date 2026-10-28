"""Offline, bounded access to a frozen v1 archive. Never imports its service."""
from collections import OrderedDict
from collections.abc import Mapping
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def load_legacy(root, module):
    """Load only an explicitly requested frozen module in a distinct namespace."""
    directory = Path(root) / "source" / "cookbook"
    package = "_v2_frozen_" + hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:12]
    if package not in sys.modules:
        pkg = types.ModuleType(package)
        pkg.__path__ = [str(directory)]
        sys.modules[package] = pkg
    name = package + "." + module
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, directory / (module + ".py"))
        obj = importlib.util.module_from_spec(spec)
        sys.modules[name] = obj
        spec.loader.exec_module(obj)
    return sys.modules[name]


class JsonlMapping(Mapping):
    """Offset index plus a byte-bounded LRU; construction never decodes a corpus."""
    def __init__(self, path, cache_bytes=8 * 1024 * 1024):
        self.path, self.cache_bytes = Path(path), cache_bytes
        self.offsets, self.cache, self.cached_bytes = {}, OrderedDict(), 0
        with self.path.open("rb") as stream:
            while True:
                start = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                record = json.loads(line)
                if record["id"] in self.offsets:
                    raise ValueError("Duplicate archive record: " + record["id"])
                self.offsets[record["id"]] = (start, len(line))

    def __iter__(self):
        return iter(self.offsets)

    def __len__(self):
        return len(self.offsets)

    def __getitem__(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key][0]
        start, size = self.offsets[key]
        with self.path.open("rb") as stream:
            stream.seek(start)
            record = json.loads(stream.read(size))
        if size <= self.cache_bytes:
            while self.cache and self.cached_bytes + size > self.cache_bytes:
                _, (_, removed) = self.cache.popitem(last=False)
                self.cached_bytes -= removed
            self.cache[key] = (record, size)
            self.cached_bytes += size
        return record


def edition_id(record):
    source = record.get("source") or {}
    identity = record.get("book_id") or source.get("book_id")
    if not identity:
        identity = source.get("sha256")
    if not identity:
        raise ValueError("No edition identity; title-only merges are forbidden")
    return "book:" + identity


def _values(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def citation(record, locator=None, scope="item"):
    source = dict(record.get("source") or {})
    locator = locator or {}
    if scope == "item" and not source.get("location_scope"):
        for field in ("printed_pages", "pdf_pages"):
            if record.get(field):
                source[field] = record[field]
    # Supplied excerpt coordinates replace item coordinates, never inherit pages.
    keys = ("path", "pages", "pdf_page", "pdf_pages", "printed_page", "printed_pages", "member", "anchor")
    if any(locator.get(k) for k in keys):
        source = {k: v for k, v in source.items() if k not in keys}
        source.update(locator)
        scope = "block"
    fmt = source.get("format") or Path(source.get("file", "")).suffix.lstrip(".").lower()
    if fmt not in {"pdf", "epub", "image", "other"}:
        fmt = "unknown"
    labels = source.get("printed_pages", source.get("printed_page", source.get("pages")))
    pages = source.get("pdf_pages", source.get("pdf_page"))
    result = dict(edition_id=edition_id(record), asset_sha256=source.get("sha256"),
                  book=record.get("book") or source.get("book") or "Unknown book",
                  author=record.get("author") or source.get("author"), format=fmt,
                  printed_labels=[str(x) for x in _values(labels)],
                  pdf_file_pages=[int(x) for x in _values(pages)],
                  epub_member=(source.get("member") or source.get("path")) if fmt == "epub" else None,
                  epub_anchor=source.get("anchor") if fmt == "epub" else None,
                  location_scope=scope)
    result["id"] = "citation:" + digest(result)[:24]
    return result


def at_path(record, path):
    value = record
    for part in path:
        if type(part) not in (str, int) or (type(part) is int and part < 0):
            raise ValueError("Invalid archive path")
        value = value[part]
    return value


def source_span(record, path, quote=None, citation_id=None, record_sha256=None):
    text = at_path(record, path)
    if not isinstance(text, str):
        raise ValueError("A source span must bind one archived string")
    quote = text if quote is None else quote
    if not quote or quote not in text:
        raise ValueError("Selected text is not an exact archived excerpt")
    start = text.index(quote)
    parent = at_path(record, path[:-1]) if path else {}
    existing = parent.get("id") if isinstance(parent, dict) else None
    record_sha256 = record_sha256 or digest(record)
    return dict(source_record_sha256=record_sha256,
                source_block_id=existing or "source:" + digest([record_sha256, path])[:32],
                legacy_path=list(path), start=start, end=start + len(quote),
                text_sha256=hashlib.sha256(quote.encode()).hexdigest(),
                citation_id=citation_id or citation(record)["id"])
