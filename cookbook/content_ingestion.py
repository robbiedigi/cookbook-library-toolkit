"""Preserve recipe commentary as linked passages and account for semantic labels.

Structural roles come from the importer. They are not semantic classifications:
a headnote can contain a story, advice, both, or neither.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .content_labels import ContentLabels, TAXONOMY_VERSION, input_hash, digest

REPORT_FILENAME = "content_label_import_report.json"


def recipe_context_sections(recipes):
    """Project contiguous headnote/note blocks without changing recipe records.

Retain original block evidence and correction ledgers; link back to the complete
recipe. Never infer a semantic category from the structural role or heading.
"""
    sections = []
    for recipe in recipes:
        groups = []
        for i, block in enumerate(recipe.get("blocks", [])):
            if block.get("kind") not in {"headnote", "note"} or not block.get("text"):
                continue
            if not groups or groups[-1][-1] != i - 1:
                groups.append([])
            groups[-1].append(i)
        for indices in groups:
            blocks = []
            for i in indices:
                block = deepcopy(recipe["blocks"][i])
                # A recipe's start location is not the location of every note.
                # Keep edition-level provenance; populate locators only from
                # this block (unknown stays null).
                origin = recipe["source"]
                loc = {k: deepcopy(origin[k]) for k in ("file", "sha256", "extractor") if k in origin}
                loc.update(book_id=recipe["book_id"], book=recipe["book"], author=recipe["author"],
                           format=origin.get("format", Path(origin.get("file", "")).suffix.lstrip(".").lower() or "unknown"),
                           member=block.get("path"), anchor=next(iter(block.get("anchors", [])), None),
                           element_index=block.get("element_index"),
                           printed_page=next(iter(block.get("pages", [])), None),
                           pdf_page=next(iter(block.get("pdf_pages", [])), None),
                           pdf_pages=deepcopy(block.get("pdf_pages", [])),
                           printed_pages=deepcopy(block.get("pages", [])))
                loc.update(deepcopy(block.get("source", {})))
                loc.update(recipe_id=recipe["id"], recipe_block_index=i)
                block["source"] = loc
                blocks.append(block)
            key = "recipe-context-1|" + recipe["id"] + "|" + ",".join(map(str, indices))
            sections.append(dict(
                id="s_" + hashlib.sha256(key.encode()).hexdigest()[:16],
                title=recipe["title"], headings=[recipe["book"], recipe["title"]],
                source=deepcopy(blocks[0]["source"]), blocks=blocks,
                recipe_context=dict(recipe_id=recipe["id"], block_indices=indices,
                                    relationship="recipe_commentary", provenance_kind="structural_projection"),
                recipe_normalizations=[deepcopy(n) for n in recipe.get("normalizations", [])
                                       if n.get("block_index") in indices],
                flags=list(recipe.get("warnings", [])) + [
                    "Recipe commentary excerpt; read the linked recipe for complete context; not a verified complete recipe.",
                    "Structural headnote/note roles do not establish content labels or personal testing."]
            ))
    return sections


def classification_report(data_dir, passages):
    """Deterministic import inventory; does not infer labels or edit a sidecar."""
    by_id = {p["id"]: p for p in passages}
    if len(by_id) != len(passages):
        raise ValueError("Duplicate source passage IDs")
    labels = ContentLabels(data_dir, by_id)
    pending, review, pending_label_reviews = [], [], []
    for p in passages:
        annotation = labels.annotations[p["id"]]
        row = dict(passage_id=p["id"], input_sha256=input_hash(p),
                   book_id=p["source"]["book_id"], reason=annotation["reason"])
        if annotation["unassessed_labels"]:
            pending_label_reviews.append({**row, "labels": annotation["unassessed_labels"]})
        if annotation["reason"] in {"not_processed", "source_changed"}:
            pending.append(row)
        elif annotation["uncertain_labels"]:
            review.append(row)
    return dict(
        version="content-ingestion-1", taxonomy_version=TAXONOMY_VERSION,
        classification_location="local_only", automatic_classification=False,
        annotation_version=labels.version,
        input_manifest_sha256=digest([(p["id"], input_hash(p)) for p in passages]),
        passages=len(passages), pending_count=len(pending), review_count=len(review),
        current_judgments=len(passages) - len(pending),
        reason_counts=dict(Counter(a["reason"] for a in labels.annotations.values())),
        recipe_context_passages=sum("recipe_context" in p for p in passages),
        passages_with_block_spans=sum("block_spans" in p for p in passages),
        orphaned_annotation_count=len(set(labels.records) - set(by_id)),
        pending=pending, review=review,
        pending_label_review_count=len(pending_label_reviews),
        pending_label_reviews=pending_label_reviews,
        label_assessment_counts=labels.status()["label_assessment_counts"],
    )


def write_classification_report(data_dir, passages):
    report = classification_report(data_dir, passages)
    target = Path(data_dir) / REPORT_FILENAME
    pending = target.with_suffix(".tmp")
    pending.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    pending.replace(target)
    return {k: v for k, v in report.items() if k not in {"pending", "review", "pending_label_reviews"}}
