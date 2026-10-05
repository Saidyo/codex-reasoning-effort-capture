"""Exact JSON paths verified against the 2026-10-05 live capture."""
from collections import Counter
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

RESPONSE_HEADERS = ('content-type', 'date', 'server', 'via', 'x-new-api-routed-channel-id',
                    'x-new-api-version', 'x-oneapi-request-id', 'transfer-encoding')
CSV_FIELDS = ('sample', 'captured_at', 'thread_id', 'turn_id', 'request_model',
              'response_model', 'requested_effort', 'response_effort', 'effort_differs',
              'reasoning_tokens', 'response_id', 'upstream_request_id', 'routed_channel_id',
              'http_status', 'response_completed', 'execution_verified')


def json_write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def request_info(request):
    info = {'method': request.method, 'uri': request.uri,
            'body_sha256': hashlib.sha256(request.body).hexdigest(),
            'client_request_id': request.headers.get('x-client-request-id')}
    raw = request.body
    encoding = request.headers.get('content-encoding')
    try:
        if encoding == 'gzip':
            raw = gzip.decompress(raw)
        elif encoding not in (None, 'identity'):
            raise ValueError('Unsupported request content encoding')
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError('Request JSON is not an object')
        info['body_keys'] = list(body)
        for key in ('model', 'reasoning', 'service_tier', 'stream'):
            if key in body:
                info[key] = body[key]
        metadata = body.get('client_metadata')
        if isinstance(metadata, dict):
            info['client_metadata'] = {key: metadata[key] for key in ('thread_id', 'turn_id', 'session_id') if key in metadata}
        info['json_decoded'] = True
    except (ValueError, UnicodeError, OSError):
        info['json_decoded'] = False
        info['content_encoding'] = encoding
    return info


def sse_events(raw):
    text = raw.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    for block in text.split('\n\n')[:-1]:
        lines = [line[5:].lstrip(' ') for line in block.split('\n') if line.startswith('data:')]
        if not lines:
            continue
        payload = '\n'.join(lines)
        if payload == '[DONE]':
            continue
        value = json.loads(payload)
        if isinstance(value, dict):
            yield value


def nested(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def inspect_response(raw):
    snapshots, counts, completed = [], Counter(), None
    parse_error = False
    try:
        for event in sse_events(raw):
            event_type = event.get('type')
            if isinstance(event_type, str):
                counts[event_type] += 1
            response = event.get('response')
            if not isinstance(response, dict):
                continue
            snapshot = {'event': event_type, 'sequence_number': event.get('sequence_number')}
            for key in ('id', 'status', 'model', 'reasoning', 'service_tier', 'usage'):
                if key in response:
                    snapshot[key] = response[key]
            snapshots.append(snapshot)
            if event_type == 'response.completed':
                completed = snapshot
    except (ValueError, UnicodeError):
        parse_error = True
    return snapshots, dict(counts), completed, parse_error


def save_exchange(out, number, port, request, response, captured_at=None):
    raw = response.body
    snapshots, counts, completed, parse_error = inspect_response(raw)
    request_effort = nested(request, 'reasoning', 'effort')
    response_effort = nested(completed, 'reasoning', 'effort')
    headers = {key: response.headers[key] for key in RESPONSE_HEADERS if key in response.headers}
    stem = f'{number:04d}'
    body_name = f'{stem}.response.sse'
    (out / body_name).write_bytes(raw)
    record = {
        'sample': number, 'captured_at': captured_at or datetime.now(timezone.utc).isoformat(), 'client_port': port,
        'thread_id': nested(request, 'client_metadata', 'thread_id'),
        'turn_id': nested(request, 'client_metadata', 'turn_id'),
        'request_model': request.get('model'), 'response_model': nested(completed, 'model'),
        'requested_effort': request_effort, 'response_effort': response_effort,
        'effort_differs': request_effort != response_effort if request_effort is not None and response_effort is not None else None,
        'reasoning_tokens': nested(completed, 'usage', 'output_tokens_details', 'reasoning_tokens'),
        'response_id': nested(completed, 'id'),
        'upstream_request_id': headers.get('x-oneapi-request-id'),
        'routed_channel_id': headers.get('x-new-api-routed-channel-id'),
        'http_status': int(response.status), 'response_completed': completed is not None,
        'execution_verified': False,
        'evidence_scope': 'Observed between Codex and the local proxy; upstream execution is not independently verified.',
        'request': request, 'response_headers': headers, 'response_snapshots': snapshots,
        'event_counts': counts, 'sse_parse_error': parse_error, 'response_body_file': body_name,
        'response_body_sha256': hashlib.sha256(raw).hexdigest(),
    }
    json_write(out / f'{stem}.json', record)
    with (out / 'records.jsonl').open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    csv_path = out / 'summary.csv'
    first = not csv_path.exists()
    with csv_path.open('a', encoding='utf-8-sig' if first else 'utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction='ignore')
        if first:
            writer.writeheader()
        writer.writerow(record)
    return record
