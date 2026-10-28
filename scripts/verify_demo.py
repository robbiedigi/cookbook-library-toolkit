"""Verify every public canonical record through the four-tool service, offline."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from library_v2.contracts import validate_output
from library_v2.service import SnapshotService
from evaluate_v2_delivery import reconstruct


def verify(library):
    library = Path(library)
    service = SnapshotService(library / "library.sqlite", "verification-only-cursor-key-32bytes")
    start = time.perf_counter()
    checked, blocks, pages, max_bytes = 0, 0, 0, 0
    try:
        assert service.snapshot.metadata["counts"] == {"books": 2, "recipe": 12, "knowledge": 723}, "Pinned demo coverage changed; inspect the import and update the evaluation explicitly."
        for entity, filename, tool, argument in (("recipe", "recipes.jsonl", "get_recipes", "recipe_ids"),
                                                  ("knowledge", "knowledge.jsonl", "get_passages", "passage_ids")):
            for line in (library / filename).read_text(encoding="utf-8").splitlines():
                original = json.loads(line)
                parts, cursor = [], None
                while True:
                    response = getattr(service, tool)(**{argument: [original["id"]], "cursor": cursor})
                    validate_output(response)
                    assert not response["errors"], response["errors"]
                    assert all(root["status"] != "error" for root in response["roots"])
                    pages += 1
                    max_bytes = max(max_bytes, len(json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()))
                    parts.extend(response["items"])
                    cursor = response["next_cursor"]
                    if cursor is None:
                        break
                delivered = reconstruct(parts)
                section, field = ("method", "method_blocks") if entity == "recipe" else ("blocks", "blocks")
                texts = [block if isinstance(block, str) else block["text"] for block in delivered[section]]
                assert texts == [block["text"] for block in original[field]], original["id"]
                assert delivered["id"] == original["id"]
                assert delivered["dependency_coverage"]["status"] == "unknown"
                assert delivered["sources"][0]["document"] == original["citations"][0]["document"]
                assert {q["code"] for q in delivered["qualifications"]} == {q["code"] for q in original["uncertainties"]}
                checked += 1
                blocks += len(texts)
        ids, cursor = [], None
        while True:
            page = service.search_recipes(limit=3, cursor=cursor)
            validate_output(page)
            assert not page["errors"]
            ids.extend(item["id"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert len(ids) == len(set(ids)) == 12
        assert checked == 735 and blocks == 1225, "Pinned canonical text coverage changed"
        assert service.search_recipes(query="SOUP")["items"]
        assert service.search_knowledge(query="pastry")["items"]
        unknown = service.get_recipes(["does-not-exist"])
        assert unknown["roots"][0]["status"] == "error"
        return dict(status="passed", snapshot=service.snapshot.id, counts=service.snapshot.metadata["counts"],
                    records_read=checked, source_blocks_compared=blocks, response_pages=pages,
                    largest_response_bytes=max_bytes, database_bytes=(library / "library.sqlite").stat().st_size,
                    elapsed_seconds=round(time.perf_counter() - start, 3),
                    scope="Exact canonical-text delivery and source locators; not independent full-book extraction or culinary validation.")
    finally:
        service.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path("data/demo"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.dumps(verify(args.library), indent=2) + "\n"
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    print(report, end="")
