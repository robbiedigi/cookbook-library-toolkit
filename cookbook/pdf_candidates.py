"""Review-only Docling snapshots. This module never updates the live corpus."""

from copy import deepcopy
import hashlib


def candidate_blocks(document, source):
    """Traverse document order, preserving all page/geometry/character evidence.

    Keep tables and pictures as structured blocks, not generated prose. A joined
    paragraph is emitted once with every source page, rather than duplicated.
    """
    blocks = []
    seen = set()

    def visit(ref):
        if ref in seen:
            raise ValueError(f"Duplicate or cyclic Docling reference: {ref}")
        seen.add(ref)
        parts = ref.split("/")
        if len(parts) != 3 or parts[0] != "#":
            raise ValueError(f"Unsupported Docling reference: {ref}")
        node = document[parts[1]][int(parts[2])]
        if parts[1] == "groups":
            for child in node.get("children", []):
                visit(child["$ref"])
            return
        provenance = deepcopy(node.get("prov", []))
        if not provenance:
            raise ValueError(f"Missing PDF provenance: {ref}")
        for prov in provenance:
            if str(prov["page_no"]) not in document["pages"]:
                raise ValueError(f"Unresolved PDF page: {prov['page_no']}")
        identity = f"{source['sha256']}:{source['group']}:{ref}"
        blocks.append({
            "id": "pdf_candidate_" + hashlib.sha256(identity.encode()).hexdigest()[:20],
            "kind": node.get("label", parts[1]),
            "text": node.get("text", ""),
            "original_text": node.get("orig"),
            "source": deepcopy(source),
            "pdf_pages": sorted({p["page_no"] for p in provenance}),
            "printed_page": None,
            "provenance": provenance,
            "raw_ref": ref,
            "raw_node": deepcopy(node),
            "status": "extracted_for_review",
            "verified_complete_recipe": False,
        })
        for child in node.get("children", []):
            visit(child["$ref"])

    for child in document["body"]["children"]:
        visit(child["$ref"])
    return blocks
