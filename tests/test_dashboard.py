import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from dashboard import DashboardServer
from dashboard_controller import CaptureController
from dashboard_store import CaptureStore


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'one'
        self.run.mkdir()
        self.log = self.run / 'records.jsonl'
        self.store = CaptureStore(self.root)

    def test_incremental_partial_line_and_bounded_history(self):
        records = [{'sample': i + 1, 'effort_differs': i % 2 == 0} for i in range(400)]
        self.log.write_bytes(b''.join(json.dumps(r).encode() + b'\n' for r in records) + b'{"sample":401')
        first = self.store.read('one')
        self.assertEqual((first['total'], len(first['rows']), first['different']), (400, 300, 200))
        self.assertEqual(first['rows'][0]['sample'], 400)
        self.assertEqual(self.store.read('one')['total'], 400)
        with self.log.open('ab') as handle:
            handle.write(b',"effort_differs":null}\n')
        self.assertEqual(self.store.read('one')['unknown'], 1)
        self.assertEqual(self.store.read('one')['total'], 401)

    def test_truncation_resets_totals(self):
        self.log.write_text('{"sample":1,"effort_differs":true}\n', encoding='utf-8')
        self.store.read('one')
        self.log.write_text('{}\n', encoding='utf-8')
        result = self.store.read('one')
        self.assertEqual((result['total'], result['different']), (1, 0))

    def test_path_escape_is_rejected(self):
        for value in ['../one', '..', 'C:\\Windows', '/one', 'one/..']:
            with self.assertRaises(ValueError):
                self.store.directory(value)

    def test_status_torn_write_preserves_last_valid_value(self):
        status = self.run / 'status.json'
        status.write_text('{"state":"capturing"}', encoding='utf-8')
        self.assertEqual(self.store.read('one')['status']['state'], 'capturing')
        status.write_text('{', encoding='utf-8')
        self.assertEqual(self.store.read('one')['status']['state'], 'capturing')


class FakePcap:
    closed = []
    def __init__(self, _):
        pass
    def open(self):
        pass
    def close(self):
        self.closed.append(True)


class ControllerTests(unittest.TestCase):
    def test_stop_and_duplicate_start_release_capture(self):
        entered = threading.Event()
        def runner(args, pcap, stop_event, on_start):
            on_start(Path('test-run'))
            entered.set()
            stop_event.wait(2)
        FakePcap.closed = []
        controller = CaptureController('.', FakePcap, runner)
        self.addCleanup(controller.close)
        controller.start({})
        self.assertTrue(entered.wait(1))
        self.assertEqual(controller.snapshot()['state'], 'capturing')
        self.assertEqual(controller.snapshot()['options'], {'port': 18080, 'seconds': 0, 'thread_id': ''})
        with self.assertRaises(RuntimeError):
            controller.start({})
        controller.close()
        self.assertFalse(controller.thread.is_alive())
        self.assertEqual(controller.snapshot()['state'], 'stopped')
        self.assertEqual(FakePcap.closed, [True])

    def test_device_failure_is_reported(self):
        class Broken(FakePcap):
            def open(self):
                raise RuntimeError('device unavailable')
        controller = CaptureController('.', Broken)
        controller.start({})
        controller.thread.join(1)
        self.assertEqual(controller.snapshot()['error'], 'device unavailable')
        self.assertEqual(controller.snapshot()['state'], 'failed')

    def test_configuration_rejects_invalid_types_and_ports(self):
        controller = CaptureController('.')
        for config in [{'port': True}, {'port': 0}, {'port': 65536}, {'seconds': -1}, {'thread_id': 123}]:
            with self.assertRaises(ValueError):
                controller.start(config)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        assets = Path(__file__).resolve().parent.parent / 'web'
        self.controller = CaptureController(self.root, FakePcap, lambda *args, **kwargs: None)
        self.server = DashboardServer(('127.0.0.1', 0), self.root, assets, controller=self.controller)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.thread.join(2)
        self.server.controller.close()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        code, data = response.status, response.read()
        connection.close()
        return code, data

    def test_page_and_empty_state(self):
        code, data = self.request('GET', '/')
        self.assertEqual(code, 200)
        self.assertIn(self.server.token.encode(), data)
        code, data = self.request('GET', '/api/state', {'X-AnyGPT-Token': self.server.token})
        self.assertEqual(code, 200)
        self.assertIsNone(json.loads(data)['selected'])

    def test_unauthorized_and_cross_origin_mutation_rejected(self):
        self.assertEqual(self.request('POST', '/api/start', body='{}')[0], 403)
        headers = {'X-AnyGPT-Token': self.server.token, 'Origin': 'https://example.org'}
        self.assertEqual(self.request('POST', '/api/start', headers, '{}')[0], 403)
        self.assertEqual(self.controller.snapshot()['state'], 'idle')

    def test_download_stream_and_traversal(self):
        run = self.root / 'run'
        run.mkdir()
        (run / 'summary.csv').write_bytes(b'a,b\r\n1,2\r\n')
        token = self.server.token
        code, data = self.request('GET', f'/api/export?run=run&token={token}')
        self.assertEqual((code, data), (200, b'a,b\r\n1,2\r\n'))
        code, _ = self.request('GET', f'/api/export?run=..&token={token}')
        self.assertEqual(code, 400)


if __name__ == '__main__':
    unittest.main()
