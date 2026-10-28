"""Resumable local Docling extraction; snapshots never modify the live library."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent.parent

BOOK_PROFILES = {
    'thai-food': ('Thai Food', 'David Thompson', True),
    'professional-chef': ('The Professional Chef, 9th edition', 'The Culinary Institute of America', False),
    'tu-casa-mi-casa': ('Tu Casa Mi Casa', 'Enrique Olvera', True),
}


def source_profile(book, profile_manifest=None):
    if profile_manifest is not None:
        profile = json.loads(Path(profile_manifest).read_text())[book]
        return profile, profile['title'], profile['author'], profile['force_ocr']
    fixtures = json.loads((ROOT / 'evals/pdf/manifest.json').read_text())
    fixtures.update(json.loads((ROOT / 'evals/pdf/book-profiles.json').read_text()))
    title, author, force_ocr = BOOK_PROFILES[book]
    return fixtures[book], title, author, force_ocr


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def run(args):
    import pypdfium2 as pdfium
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, OcrMacOptions
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend

    book = getattr(args, 'book', 'thai-food')
    fixture, title, author, force_ocr = source_profile(book, getattr(args, 'profile_manifest', None))
    source = args.sources / fixture['file']
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != fixture['sha256']:
        raise ValueError('Source differs from the reviewed edition: ' + book)
    with pdfium.PdfDocument(source) as pdf:
        count = len(pdf)
    format_option = PdfFormatOption()
    if force_ocr:
        format_option.backend = PyPdfiumDocumentBackend
    config = dict(book_id=book, title=title, author=author,
                  file=source.name, sha256=digest, pages=count, chunk_size=args.chunk_size,
                  docling=importlib.metadata.version('docling'),
                  docling_core=importlib.metadata.version('docling-core'),
                  backend=format_option.backend.__name__, ocr='Apple Vision accurate en-US',
                  force_full_page_ocr=force_ocr, remote_services=False, retain_ocr_lines=True)
    if getattr(args, 'models', None) is not None:
        config['models_path'] = str(args.models.resolve())
    ocr_scale = getattr(args, 'ocr_scale', None)
    if ocr_scale is not None:
        if ocr_scale <= 0:
            raise ValueError('OCR scale must be positive')
        config['ocr_scale'] = ocr_scale
    if config['docling'] != '2.132.0':
        raise ValueError('Use the reviewed Docling 2.132.0 environment')
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = args.output / 'manifest.json'
    if manifest.exists() and json.loads(manifest.read_text()) != config:
        raise ValueError('Changed extraction configuration requires a new output directory')
    save(manifest, config)
    models = getattr(args, 'models', None) or ROOT / '.cache/pdf-parsers/docling'
    options = PdfPipelineOptions(artifacts_path=models,
        enable_remote_services=False, do_ocr=True, do_table_structure=True,
        generate_parsed_pages=True,
        ocr_options=OcrMacOptions(lang=['en-US'], force_full_page_ocr=force_ocr,
            **({'scale': ocr_scale} if ocr_scale is not None else {})),
        accelerator_options=AcceleratorOptions(device='cpu', num_threads=4))
    format_option.pipeline_options = options
    converter = DocumentConverter(format_options={InputFormat.PDF: format_option})
    reports = []
    for first in range(1, count + 1, args.chunk_size):
        last = min(count, first + args.chunk_size - 1)
        stem = args.output / f'{first:04d}-{last:04d}'
        report_path = stem.with_suffix('.run.json')
        document_path = stem.with_suffix('.docling.json')
        lines_path = stem.with_suffix('.lines.json')
        if report_path.exists():
            report = json.loads(report_path.read_text())
            for p, key in [(document_path, 'snapshot_sha256'), (lines_path, 'lines_sha256')]:
                if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != report[key]:
                    raise ValueError('Cached snapshot changed or missing: ' + str(p))
            reports.append(report)
            continue
        print(f'Extracting PDF pages {first}-{last} / {count}', flush=True)
        started = time.monotonic()
        result = converter.convert(source, page_range=(first, last))
        save(document_path, result.document.export_to_dict())
        lines = [{ 'pdf_page': p.page_no, 'size': p.size.model_dump(mode='json'),
                   'cells': [c.model_dump(mode='json') for c in p.cells]}
                 for p in result.pages]
        save(lines_path, lines)
        expected = set(range(first, last + 1))
        if str(result.status) != 'ConversionStatus.SUCCESS' or {p['pdf_page'] for p in lines} != expected:
            raise ValueError('Incomplete conversion; raw evidence retained without success report')
        blank = [p['pdf_page'] for p in lines if not any(c.get('text', '').strip() for c in p['cells'])]
        if len(blank) == len(lines):
            raise ValueError('Entire chunk lacks OCR text; inspect native OCR access and source rendering')
        report = dict(first=first, last=last, seconds=round(time.monotonic()-started, 2),
            snapshot_sha256=hashlib.sha256(document_path.read_bytes()).hexdigest(),
            lines_sha256=hashlib.sha256(lines_path.read_bytes()).hexdigest(),
            empty_ocr_pages_for_review=blank, status='extracted_for_review')
        save(report_path, report)
        reports.append(report)
        print(f'Saved {first}-{last}; empty pages needing review: {blank}', flush=True)
    save(args.output / 'extraction-report.json', dict(config=config, chunks=reports,
        pages_processed=sum(r['last']-r['first']+1 for r in reports),
        status='extracted_for_review', verified_complete_recipes=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--book', default='thai-food')
    parser.add_argument('--profile-manifest', type=Path,
                        help='Edition-hashed batch profiles, including title, author and force_ocr')
    parser.add_argument('--sources', type=Path, default=Path.home() / 'Desktop/Cookbooks/PDF')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--models', type=Path, help='Pre-downloaded Docling model directory')
    parser.add_argument('--chunk-size', type=int, default=32)
    parser.add_argument('--ocr-scale', type=float, help='Explicit render scale for a separately recorded OCR trial')
    args = parser.parse_args()
    if args.ocr_scale is not None and args.ocr_scale <= 0:
        parser.error('ocr-scale must be positive')
    if args.chunk_size < 1:
        parser.error('chunk-size must be positive')
    os.environ.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1')
    run(args)
