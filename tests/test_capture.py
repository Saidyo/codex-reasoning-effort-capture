import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import dpkt

from protocol import Stream
from reporting import inspect_response, request_info, save_exchange


def response_wire(body, chunked=False):
    if chunked:
        return (b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n'
                + f'{len(body):x};extension=yes\r\n'.encode() + body + b'\r\n0\r\nX-Trailer: ok\r\n\r\n')
    return b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body


class ReassemblyTests(unittest.TestCase):
    def test_large_request_reordered_segments_and_retransmission(self):
        body = json.dumps({'model': 'gpt-6-astra', 'input': 'x' * 220000,
                           'reasoning': {'effort': 'xhigh'}}).encode()
        wire = b'POST /v1/responses HTTP/1.1\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body
        stream = Stream(True)
        size, base = 65495, 100
        self.assertEqual(stream.add(base, wire[:size]), [])
        self.assertEqual(stream.add(base + size * 2, wire[size * 2:size * 3]), [])
        self.assertEqual(stream.add(base, wire[:size]), [])
        self.assertEqual(stream.add(base + size, wire[size:size * 2]), [])
        messages = stream.add(base + size * 3, wire[size * 3:])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].body, body)

    def test_missing_segment_never_completes(self):
        wire = response_wire(b'a' * 200)
        stream = Stream(False)
        self.assertEqual(stream.add(50, wire[:80]), [])
        self.assertEqual(stream.add(150, wire[100:]), [])
        self.assertTrue(stream.pending)

    def test_chunked_extensions_trailers_bytewise_and_utf8(self):
        body = 'data: {"text":"中文"}\n\n'.encode()
        wire = response_wire(body, chunked=True)
        stream = Stream(False)
        messages = stream.add(0, wire[:10])
        for i in range(10, len(wire)):
            messages.extend(stream.add(i, wire[i:i + 1]))
        self.assertEqual([m.body for m in messages], [body])

    def test_sequence_wrap_and_keepalive(self):
        wire = response_wire(b'one') + response_wire(b'two')
        stream = Stream(False)
        base = 0xfffffff0
        messages = stream.add(base, wire[:30])
        messages.extend(stream.add((base + 30) & 0xffffffff, wire[30:]))
        self.assertEqual([m.body for m in messages], [b'one', b'two'])


class EvidenceTests(unittest.TestCase):
    def test_live_fixture_difference_and_no_execution_claim(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/observed.json').read_text(encoding='utf-8'))
        raw = b''.join(('data: ' + json.dumps(event) + '\n\n').encode() for event in fixture['events'])
        response = SimpleNamespace(body=raw, status='200', headers={})
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            record = save_exchange(out, 1, 18080, fixture['request'], response)
            self.assertEqual(record['requested_effort'], 'xhigh')
            self.assertEqual(record['response_effort'], 'high')
            self.assertTrue(record['effort_differs'])
            self.assertFalse(record['execution_verified'])
            self.assertTrue(record['response_completed'])
            self.assertEqual((out / '0001.response.sse').read_bytes(), raw)
            self.assertEqual(len((out / 'records.jsonl').read_text(encoding='utf-8').splitlines()), 1)

    def test_missing_effort_is_unknown_even_with_reasoning_tokens(self):
        raw = b'data: {"type":"response.completed","response":{"usage":{"output_tokens_details":{"reasoning_tokens":500}}}}\n\n'
        with tempfile.TemporaryDirectory() as folder:
            r = save_exchange(Path(folder), 1, 1, {'reasoning': {'effort': 'xhigh'}},
                              SimpleNamespace(body=raw, status='200', headers={}))
            self.assertIsNone(r['response_effort'])
            self.assertIsNone(r['effort_differs'])
            self.assertEqual(r['reasoning_tokens'], 500)

    def test_partial_or_malformed_event_does_not_complete(self):
        _, _, completed, _ = inspect_response(b'data: {"type":"response.completed"}')
        self.assertIsNone(completed)
        _, _, completed, error = inspect_response(b'data: invalid\n\n')
        self.assertIsNone(completed)
        self.assertTrue(error)

    def test_request_does_not_persist_authorization_or_prompt(self):
        body = json.dumps({'model': 'gpt-6-astra', 'input': 'PRIVATE_PROMPT',
                           'reasoning': {'effort': 'xhigh'}}).encode()
        wire = (b'POST /v1/responses HTTP/1.1\r\nAuthorization: SECRET_TOKEN\r\nContent-Length: '
                + str(len(body)).encode() + b'\r\n\r\n' + body)
        saved = json.dumps(request_info(dpkt.http.Request(wire)))
        self.assertNotIn('PRIVATE_PROMPT', saved)
        self.assertNotIn('SECRET_TOKEN', saved)


if __name__ == '__main__':
    unittest.main()
