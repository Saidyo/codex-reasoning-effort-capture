"""Opt-in Windows/Npcap integration check; uses temporary local HTTP traffic only."""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import tempfile
import threading
import time
from types import SimpleNamespace
from capture import Npcap, capture

fixture = json.loads((Path(__file__).resolve().parents[1] / 'tests/fixtures/observed.json').read_text('utf-8'))
body = b''.join(('data: ' + json.dumps(event) + '\n\n').encode() for event in fixture['events'])

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args): pass
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

class Probe:
    def __init__(self, pcap):
        self.pcap, self.fragments = pcap, []
    def packet(self):
        raw = self.pcap.packet()
        if raw and len(raw) >= 24 and raw[4] >> 4 == 4 and raw[13] == 6:
            frag = int.from_bytes(raw[10:12], 'big')
            if frag & 0x3fff:
                self.fragments.append({'length':len(raw), 'offset':frag & 8191, 'more':bool(frag & 8192)})
        return raw
    def stats(self): return self.pcap.stats()

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
pcap = Npcap(Path(os.environ['SystemRoot']) / 'System32/Npcap')
pcap.open()
probe = Probe(pcap)
stop, ready = threading.Event(), threading.Event()
with tempfile.TemporaryDirectory(prefix='anygpt-probe-') as folder:
    args = SimpleNamespace(output=Path(folder), seconds=0, count=0, port=server.server_port, thread_id=None)
    worker = threading.Thread(target=capture, args=(args, probe), kwargs={'stop_event':stop,'on_start':lambda _:ready.set()})
    worker.start()
    ready.wait(2)
    expected = 0
    for size in (200, 2200000, 6000000):
        client = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        for i in range(3):
            request = dict(fixture['request'], input='x'*size, client_metadata={'thread_id':f'local-test-{size}-{i}'})
            client.request('POST','/v1/responses',json.dumps(request),{'Content-Type':'application/json'})
            client.getresponse().read()
            expected += 1
            time.sleep(.2)
        client.close()
    time.sleep(.5)
    stop.set()
    worker.join(5)
    run = next(Path(folder).iterdir())
    status = json.loads((run/'status.json').read_text('utf-8'))
    print(json.dumps({'expected':expected,'status':status,'fragments':probe.fragments[:10],'fragment_count':len(probe.fragments)}))
    pcap.close()
    server.shutdown()
    server.server_close()
    assert status['samples'] == expected, f"Captured {status['samples']}/{expected} exchanges"
    records = [json.loads(line) for line in (run/'records.jsonl').read_text('utf-8').splitlines()]
    assert len({r['thread_id'] for r in records}) == expected
    assert all(r['requested_effort'] == 'xhigh' and r['response_effort'] == 'high' for r in records)
    assert status['npcap']['dropped'] == 0
