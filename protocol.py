"""TCP/HTTP reassembly; no application field-name inference."""
from collections import deque
import io

import dpkt

MAX_BYTES = 64 * 1024 * 1024


def http_length(buffer):
    end = buffer.find(b'\r\n\r\n')
    if end < 0:
        return None
    headers = dpkt.http.parse_headers(io.BytesIO(buffer[buffer.index(b'\r\n') + 2:end + 4]))
    pos = end + 4
    if headers.get('transfer-encoding', '').lower() == 'chunked':
        while True:
            line_end = buffer.find(b'\r\n', pos)
            if line_end < 0:
                return None
            size = int(buffer[pos:line_end].split(b';', 1)[0], 16)
            if size < 0:
                raise ValueError('Negative HTTP chunk length')
            pos = line_end + 2
            if size == 0:
                if buffer[pos:pos + 2] == b'\r\n':
                    return pos + 2
                trailer_end = buffer.find(b'\r\n\r\n', pos)
                return trailer_end + 4 if trailer_end >= 0 else None
            if len(buffer) < pos + size + 2:
                return None
            if buffer[pos + size:pos + size + 2] != b'\r\n':
                raise ValueError('Invalid HTTP chunk delimiter')
            pos += size + 2
    if 'content-length' in headers:
        size = int(headers['content-length'])
        if size < 0:
            raise ValueError('Negative HTTP content length')
        return pos + size if len(buffer) >= pos + size else None
    first = buffer[:buffer.index(b'\r\n')].split()
    if first[0].startswith(b'HTTP/') and first[1] not in (b'100', b'101', b'102', b'103', b'204', b'304'):
        raise ValueError('Response without Content-Length/chunked framing is unsupported')
    return pos


def parse_http(data, inbound):
    """Parse only complete messages. Handles chunk extensions and trailers."""
    cls = dpkt.http.Request if inbound else dpkt.http.Response
    header_end = data.index(b'\r\n\r\n') + 4
    header_bytes = data[:header_end]
    headers = dpkt.http.parse_headers(io.BytesIO(header_bytes[header_bytes.index(b'\r\n') + 2:]))
    if headers.get('transfer-encoding', '').lower() != 'chunked':
        return cls(data)
    # dpkt 1.9.8 does not accept chunk extensions; decode validated chunks here.
    parts = []
    pos = header_end
    while True:
        end = data.index(b'\r\n', pos)
        size = int(data[pos:end].split(b';', 1)[0], 16)
        pos = end + 2
        if size == 0:
            break
        parts.append(data[pos:pos + size])
        pos += size + 2
    message = cls()
    if inbound:
        method, uri, version = header_bytes.split(b'\r\n', 1)[0].decode('ascii').split()
        message.method, message.uri, message.version = method, uri, version[5:]
    else:
        first = header_bytes.split(b'\r\n', 1)[0].decode('latin-1').split(' ', 2)
        message.version, message.status = first[0][5:], first[1]
        message.reason = first[2] if len(first) == 3 else ''
    message.headers, message.body, message.data = headers, b''.join(parts), b''
    return message


class Stream:
    def __init__(self, inbound):
        self.inbound = inbound
        self.expected = None
        self.pending = {}
        self.buffer = b''

    def add(self, seq, payload):
        if self.expected is None:
            prefix = b'POST ' if self.inbound else b'HTTP/'
            if not payload.startswith(prefix):
                return []  # Capture started in the middle of a connection.
            self.expected = seq
        # Unwrap the TCP sequence relative to the current contiguous position.
        delta = (seq - self.expected) & 0xffffffff
        if delta >= 0x80000000:
            delta -= 0x100000000
        position = self.expected + delta
        if position < self.expected:
            payload = payload[self.expected - position:]
            position = self.expected
        if payload and len(payload) > len(self.pending.get(position, b'')):
            self.pending[position] = payload
        while self.pending and min(self.pending) <= self.expected:
            position = min(self.pending)
            part = self.pending.pop(position)[self.expected - position:]
            self.buffer += part
            self.expected += len(part)
        if len(self.buffer) + sum(map(len, self.pending.values())) > MAX_BYTES:
            raise ValueError('Connection exceeded 64 MiB reassembly limit')
        messages = []
        while self.buffer:
            length = http_length(self.buffer)
            if length is None:
                break
            messages.append(parse_http(self.buffer[:length], self.inbound))
            self.buffer = self.buffer[length:]
        return messages


class Connection:
    def __init__(self):
        self.streams = {True: Stream(True), False: Stream(False)}
        self.requests = deque()
        self.last_seen = 0.0

    def unfinished(self):
        return bool(self.requests or any(s.buffer or s.pending for s in self.streams.values()))
