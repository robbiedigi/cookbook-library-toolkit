"""Generate invented EPUB and OCR-contract fixtures; no downloaded cookbook text.

The OCR fixture replays retained evidence. It does not run or evaluate OCR.
"""
import argparse
import hashlib
from html import escape
import json
from pathlib import Path
from zipfile import ZipFile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def make(output):
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = root / "example.epub"
    with ZipFile(source, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/package.opf"/></rootfiles></container>')
        archive.writestr("OPS/package.opf", '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/></spine></package>')
        archive.writestr("OPS/chapter.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Invented examples</h1><p>Software fixtures, not tested recipes.</p><h2 class="rt" id="soup">Garden soup</h2><p class="hn">An invented software example.</p><p class="yield">Serves 2</p><p class="ing">2 carrots</p><p class="ing">1 cup water</p><p class="method">Simmer until tender.</p><h2 class="rt" id="held">Incomplete example</h2><p class="ing">1 apple</p></body></html>')
    dump(root / "epub-profile.json", dict(id="synthetic-epub", title="Invented cookbook", author="Example author",
        sha256=sha(source), member_pattern=r"OPS/chapter\.xhtml", layout=dict(titles="rt", ingredients="ing",
        instructions="method", yields="yield", groups="group", headnotes="hn", barriers="")))

    extraction = root / "ocr-evidence"
    extraction.mkdir()
    # A plain text source marker and SVG make the synthetic nature explicit.
    # They are not disguised as a PDF or as output from a real OCR run.
    marker = root / "synthetic-source.txt"
    marker.write_text("Invented OCR contract fixture. No PDF was scanned.\n")
    texts = ["Garden soup", "2 carrots", "Simmer until tender.", "7"]
    cells, nodes = [], []
    for i, text in enumerate(texts):
        top = 765 if i == 3 else 60 + i * 45
        rect = {"coord_origin": "TOPLEFT", **{f"r_x{n}": x for n, x in enumerate([40, 550, 550, 40])},
                **{f"r_y{n}": y for n, y in enumerate([top, top, top + 20, top + 20])}}
        cells.append(dict(text=text, rect=rect))
        nodes.append(dict(label="section_header" if i == 0 else "page_footer" if i == 3 else "text",
                          text=text, prov=[dict(page_no=1, charspan=[0, len(text)],
                          bbox=dict(l=40, r=550, t=top, b=top+20, coord_origin="TOPLEFT"))]))
    document = dict(body=dict(children=[{"$ref": f"#/texts/{i}"} for i in range(4)]),
                    texts=nodes, pictures=[], tables=[], groups=[], pages={"1": {}}, furniture=dict(children=[]))
    dump(extraction / "0001-0001.docling.json", document)
    dump(extraction / "0001-0001.lines.json", [dict(pdf_page=1, size=dict(width=600, height=800), cells=cells)])
    dump(extraction / "0001-0001.run.json", dict(first=1, last=1,
         snapshot_sha256=sha(extraction / "0001-0001.docling.json"), lines_sha256=sha(extraction / "0001-0001.lines.json")))
    dump(extraction / "manifest.json", dict(book_id="synthetic-scan", title="Invented scan fixture",
         author="Example author", file=marker.name, sha256=sha(marker), pages=1, synthetic=True))
    dump(root / "pdf-profile.json", dict(book_id="synthetic-scan", sha256=sha(marker), body=[1, 1]))
    render = root / "synthetic-page.svg"
    render.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="white"/>' +
         ''.join(f'<text x="40" y="{785 if i == 3 else 80+i*45}" fill="black">{escape(text)}</text>' for i, text in enumerate(texts)) + '</svg>')
    dump(root / "review-ledger.json", dict(source_sha256=sha(marker),
         pages={"1": dict(printed_page="7", source_render=str(render), source_render_sha256=sha(render))},
         recipes=[dict(key="soup", title="Garden soup", aliases=[], kind="recipe", parent_key=None, note="Synthetic fixture only.",
                      parts=[dict(pdf_page=1, title=[[0, 0]], ingredients=[[1, 1]], method=[[2, 2]])])],
         corrections=[], questions=[], non_recipe_cells=[]))
    return dict(output=str(root), synthetic=True, ocr_executed=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    print(json.dumps(make(parser.parse_args().output), indent=2))
