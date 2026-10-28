"""Pending review inventory for staged imports, never an acceptance receipt."""
import hashlib
import json
import re


def pending_import_review(records, source_hashes):
    """Bind newly staged records to editions without inheriting prior review status.

    Extraction links, ingredient-index links and copied dependency sidecars do
    not establish reviewed cooking dependencies. A zero-record book also needs
    source review: it may contain held candidates or extraction omissions.
    """
    if not source_hashes or any(not isinstance(value, str) or
                               not re.fullmatch(r'[0-9a-f]{64}', value)
                               for value in source_hashes.values()):
        raise ValueError('Expected explicit source SHA-256 for each imported book')
    grouped = {book: [] for book in source_hashes}
    seen = set()
    for record in records:
        if record['id'] in seen:
            raise ValueError('Duplicate imported record ID')
        seen.add(record['id'])
        if record['book_id'] not in grouped:
            raise ValueError('Imported record lacks a source edition')
        edition = record.get('source', {}).get('sha256')
        if edition is not None and edition != source_hashes[record['book_id']]:
            raise ValueError('Imported record source edition differs from review plan')
        grouped[record['book_id']].append(record)
    books = []
    for book, rows in sorted(grouped.items()):
        ordered = sorted(rows, key=lambda row: row['id'])
        encoded = json.dumps(ordered, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()
        books.append(dict(book_id=book, source_sha256=source_hashes[book],
                          imported_record_ids=[row['id'] for row in ordered],
                          imported_record_count=len(rows),
                          imported_records_sha256=hashlib.sha256(encoded).hexdigest(),
                          dependency_coverage='unreviewed',
                          visual_source_review='pending'))
    return dict(schema='cookbook-import-review-plan-1', status='pending_review',
                acceptance_claimed=False, books=books,
                workflow='docs/new-book-import.md',
                default_next_step='Review dependencies and useful visuals during this import; '
                                  'source-audit and evaluate changed evidence before acceptance.',
                discrepancy_policy='Preserve ordinary source quirks as cooking-time notes; '
                                   'check missing references against full source once; '
                                   'retain consequential unresolved evidence without guessing.')
