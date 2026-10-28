"""Best-effort, metadata-only activity events. Never serialize request bodies."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import sys
import time
import uuid

LOGGER = logging.getLogger('cookbook.activity')
TOOLS = frozenset(('search_recipes', 'get_recipes', 'search_knowledge', 'get_passages'))
ERROR_CODES = frozenset(('NOT_FOUND', 'UNKNOWN_BOOK', 'AMBIGUOUS_BOOK', 'STALE_REFERENCE',
    'AMBIGUOUS_REFERENCE', 'INVALID_CURSOR', 'CURSOR_INPUT_MISMATCH', 'SNAPSHOT_UNAVAILABLE',
    'INVALID_ARGUMENT', 'DEPENDENCY_LIMIT', 'DEPENDENCY_CYCLE', 'STALE_REVIEW',
    'UNRESOLVED_REFERENCE', 'CONTENT_TOO_LARGE', 'UNSUPPORTED_POLICY'))


class PrivateExceptionFilter(logging.Filter):
    """Framework tracebacks may include rejected inputs; retain only their type."""
    def filter(self, record):
        if record.exc_info or str(record.msg).startswith('Tool '):
            kind = record.exc_info[0].__name__ if record.exc_info else 'ToolError'
            record.msg = 'Request failed (%s); consult library_activity metadata'
            record.args = (kind,)
            record.exc_info = record.exc_text = record.stack_info = None
        return True


def configure_logging():
    """Use stdout JSON without changing other libraries' log configuration."""
    if not LOGGER.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter('%(message)s'))
        LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    for name in ('mcp.server.mcpserver.server', 'uvicorn.error'):
        logger = logging.getLogger(name)
        if not any(isinstance(f, PrivateExceptionFilter) for f in logger.filters):
            logger.addFilter(PrivateExceptionFilter())


def emit(event):
    # A broken logging destination must not break a library read.
    try:
        LOGGER.info(json.dumps(event, separators=(',', ':'), ensure_ascii=True))
    except Exception:
        pass


class Activity:
    def __init__(self, tool, transport, token, snapshot):
        self.started = time.monotonic()
        self.event = dict(event='library_activity', version=1, level='info',
            message='Library request completed', timestamp=datetime.now(timezone.utc).isoformat(),
            request_id=uuid.uuid4().hex, user_id=token.subject if token else None,
            client_id=token.client_id if token else None,
            tool=tool if tool in TOOLS else 'unknown', transport=transport,
            snapshot=snapshot, outcome='error', error_codes=[], result_count=0,
            requested_ids=[], requested_count=0, continuation=False, has_more=False)

    def result(self, arguments, payload):
        # Read IDs only from successfully resolved roots, never arbitrary input
        # strings or error IDs. Later continuation pages may omit root metadata.
        key = {'get_recipes': 'recipe_ids', 'get_passages': 'passage_ids'}.get(self.event['tool'])
        ids = arguments.get(key, []) if key else []
        self.event['requested_count'] = len(ids) if isinstance(ids, list) else 0
        self.event['continuation'] = bool(arguments.get('cursor'))
        self.event['has_more'] = bool(payload.get('next_cursor'))
        self.event['requested_ids'] = [r['id'] for r in payload.get('roots', [])
            if r.get('status') == 'ok' and r['id'] in ids]
        errors = list(payload.get('errors', []))
        errors.extend(r['error'] for r in payload.get('roots', []) if r.get('error'))
        self.event['error_codes'] = sorted({e['code'] if e.get('code') in ERROR_CODES else 'OTHER'
            for e in errors})
        self.event['result_count'] = len(payload.get('items', []))
        self.event['outcome'] = ('partial' if self.event['result_count'] else 'error') if errors else 'success'

    def fail(self, code):
        self.event.update(outcome='error', error_codes=[code])


@contextmanager
def activity(tool, transport, token, snapshot):
    record = Activity(tool, transport, token, snapshot)
    try:
        yield record
    except BaseException:
        # Exception messages/tracebacks can contain queries, tokens or source text.
        record.fail('REQUEST_FAILED')
        raise
    finally:
        record.event['duration_ms'] = round((time.monotonic() - record.started) * 1000, 2)
        emit(record.event)


class AuthFailureLogging:
    """MCP rejects authentication before tool dispatch; do not inspect its body."""
    def __init__(self, app, snapshot):
        self.app, self.snapshot = app, snapshot

    async def __call__(self, scope, receive, send):
        async def observed(message):
            if message['type'] == 'http.response.start' and message['status'] in (401, 403):
                with activity('unknown', 'mcp', None, self.snapshot) as record:
                    record.fail('UNAUTHORIZED')
            await send(message)
        await self.app(scope, receive, observed if scope['type'] == 'http' and scope['path'] == '/mcp' else send)
