"""Passive Windows loopback capture for Codex Responses traffic."""
import argparse
import ctypes as C
from datetime import datetime
import os
from pathlib import Path
import sys
import time
import uuid

import dpkt

from protocol import Connection
from reporting import json_write, request_info, save_exchange


class PacketHeader(C.Structure):
    _fields_ = [('seconds', C.c_long), ('microseconds', C.c_long),
                ('caplen', C.c_uint), ('wirelen', C.c_uint)]


class Filter(C.Structure):
    _fields_ = [('length', C.c_uint), ('instructions', C.c_void_p)]


class Device(C.Structure):
    pass


Device._fields_ = [('next', C.POINTER(Device)), ('name', C.c_char_p),
                   ('description', C.c_char_p), ('addresses', C.c_void_p), ('flags', C.c_uint)]


class Statistics(C.Structure):
    _fields_ = [('received', C.c_uint), ('dropped', C.c_uint), ('interface_dropped', C.c_uint)]


class Npcap:
    def __init__(self, dll_dir):
        if os.name != 'nt':
            raise RuntimeError('This capture backend requires Windows and Npcap.')
        self.dll_cookie = os.add_dll_directory(str(dll_dir))
        self.lib = C.CDLL(str(dll_dir / 'wpcap.dll'))
        signatures = {
            'pcap_findalldevs': ([C.POINTER(C.POINTER(Device)), C.c_char_p], C.c_int),
            'pcap_freealldevs': ([C.POINTER(Device)], None),
            'pcap_create': ([C.c_char_p, C.c_char_p], C.c_void_p),
            'pcap_set_snaplen': ([C.c_void_p, C.c_int], C.c_int),
            'pcap_set_timeout': ([C.c_void_p, C.c_int], C.c_int),
            'pcap_set_buffer_size': ([C.c_void_p, C.c_int], C.c_int),
            'pcap_activate': ([C.c_void_p], C.c_int),
            'pcap_datalink': ([C.c_void_p], C.c_int),
            'pcap_compile': ([C.c_void_p, C.POINTER(Filter), C.c_char_p, C.c_int, C.c_uint], C.c_int),
            'pcap_setfilter': ([C.c_void_p, C.POINTER(Filter)], C.c_int),
            'pcap_freecode': ([C.POINTER(Filter)], None),
            'pcap_setnonblock': ([C.c_void_p, C.c_int, C.c_char_p], C.c_int),
            'pcap_geterr': ([C.c_void_p], C.c_char_p),
            'pcap_next_ex': ([C.c_void_p, C.POINTER(C.POINTER(PacketHeader)), C.POINTER(C.POINTER(C.c_ubyte))], C.c_int),
            'pcap_stats': ([C.c_void_p, C.POINTER(Statistics)], C.c_int),
            'pcap_close': ([C.c_void_p], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, result
        self.handle = None

    def devices(self):
        first = C.POINTER(Device)()
        error = C.create_string_buffer(256)
        if self.lib.pcap_findalldevs(C.byref(first), error) != 0:
            raise RuntimeError(error.value.decode(errors='replace'))
        result, item = [], first
        try:
            while item:
                result.append((item.contents.name.decode(), (item.contents.description or b'').decode(errors='replace')))
                item = item.contents.next
        finally:
            if first:
                self.lib.pcap_freealldevs(first)
        return result

    def open(self):
        name = '\\Device\\NPF_Loopback'
        if name not in dict(self.devices()):
            raise RuntimeError('Npcap loopback interface is not available.')
        error = C.create_string_buffer(256)
        self.handle = self.lib.pcap_create(name.encode(), error)
        if not self.handle:
            raise RuntimeError(error.value.decode(errors='replace'))
        # The default buffer dropped 2.2/6 MB request bursts in the live
        # regression. Reserve a bounded 8 MiB before activating capture.
        for option, value in (('pcap_set_snaplen', 1048576), ('pcap_set_timeout', 100),
                              ('pcap_set_buffer_size', 8 * 1024 * 1024)):
            if getattr(self.lib, option)(self.handle, value) != 0:
                raise RuntimeError(self.lib.pcap_geterr(self.handle).decode())
        if self.lib.pcap_activate(self.handle) < 0:
            raise RuntimeError(self.lib.pcap_geterr(self.handle).decode())
        if self.lib.pcap_datalink(self.handle) != 0:
            raise RuntimeError('Loopback link type is not DLT_NULL; capture stopped.')
        program = Filter()
        # Live reproduction: Npcap 1.83's port BPF lost subsequent large-request
        # segments. This IP filter retained contiguous sequence numbers.
        expression = b'ip proto 6 and host 127.0.0.1'
        if self.lib.pcap_compile(self.handle, C.byref(program), expression, 1, 0xffffffff) != 0:
            raise RuntimeError(self.lib.pcap_geterr(self.handle).decode())
        try:
            if self.lib.pcap_setfilter(self.handle, C.byref(program)) != 0:
                raise RuntimeError(self.lib.pcap_geterr(self.handle).decode())
        finally:
            self.lib.pcap_freecode(C.byref(program))
        if self.lib.pcap_setnonblock(self.handle, 1, error) != 0:
            raise RuntimeError(error.value.decode(errors='replace'))

    def packet(self):
        header, data = C.POINTER(PacketHeader)(), C.POINTER(C.c_ubyte)()
        status = self.lib.pcap_next_ex(self.handle, C.byref(header), C.byref(data))
        if status == 0:
            return None
        if status < 0:
            raise RuntimeError(f'pcap_next_ex returned {status}')
        if header.contents.caplen != header.contents.wirelen:
            raise RuntimeError('Npcap truncated a packet; refusing to label it a complete capture.')
        return C.string_at(data, header.contents.caplen)

    def stats(self):
        # Reserve space for Npcap's platform extension, while reading only the
        # three portable pcap_stat counters used by the report.
        storage = C.create_string_buffer(256)
        pointer = C.cast(storage, C.POINTER(Statistics))
        if self.handle and self.lib.pcap_stats(self.handle, pointer) == 0:
            return {name: getattr(pointer.contents, name) for name, _ in Statistics._fields_}
        return None

    def close(self):
        if self.handle:
            self.lib.pcap_close(self.handle)
            self.handle = None
        self.dll_cookie.close()


def capture(args, pcap, *, stop_event=None, on_start=None):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    out = root / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    out.mkdir()
    if on_start is not None:
        on_start(out)
    connections, samples, packets, errors, unfinished_closed = {}, 0, 0, 0, 0
    start = time.monotonic()
    last_status = start
    state = 'stopped'
    idle_delay = 0.01
    print(f'输出目录: {out}', flush=True)
    print(f'正在抓取 127.0.0.1:{args.port} 的 /v1/responses；正常使用 Codex 即可。Ctrl+C 停止。', flush=True)
    print('响应中的 effort 是服务端报告值，不是内部执行强度的独立证明。', flush=True)
    json_write(out / 'status.json', {'state': 'capturing', 'port': args.port, 'samples': 0})
    try:
        while (args.seconds == 0 or time.monotonic() - start < args.seconds) and (args.count == 0 or samples < args.count):
            if (stop_event is not None and stop_event.is_set()) or (out / 'STOP').exists():
                break
            now = time.monotonic()
            if now - last_status > 5:
                for key in list(connections):
                    if now - connections[key].last_seen > 300:
                        unfinished_closed += int(connections[key].unfinished())
                        del connections[key]
                json_write(out / 'status.json', {'state': 'capturing', 'port': args.port,
                           'samples': samples, 'packets': packets, 'errors': errors,
                           'unfinished_connections': unfinished_closed + sum(c.unfinished() for c in connections.values()),
                           'npcap': pcap.stats()})
                last_status = now
            raw = pcap.packet()
            if raw is None:
                idle_delay = min(idle_delay * 1.5, 0.1)
                if stop_event is None:
                    time.sleep(idle_delay)
                else:
                    stop_event.wait(idle_delay)
                continue
            idle_delay = 0.01
            packets += 1
            if len(raw) < 24 or raw[4] >> 4 != 4 or raw[13] != 6:
                continue
            ip_bytes = raw[4:]
            if int.from_bytes(ip_bytes[6:8], 'big') & 0x3fff:
                continue  # Fragmented IPv4 is outside this loopback backend.
            ip_header_length = (ip_bytes[0] & 15) * 4
            try:
                tcp = dpkt.tcp.TCP(ip_bytes[ip_header_length:])
            except dpkt.UnpackError:
                continue
            if args.port not in (tcp.sport, tcp.dport):
                continue
            inbound = tcp.dport == args.port
            port = tcp.sport if inbound else tcp.dport
            key = (ip_bytes[12:16], port, ip_bytes[16:20]) if inbound else (ip_bytes[16:20], port, ip_bytes[12:16])
            if inbound and tcp.flags & dpkt.tcp.TH_SYN:
                old = connections.pop(key, None)
                unfinished_closed += int(old is not None and old.unfinished())
            connection = connections.setdefault(key, Connection())
            connection.last_seen = now
            try:
                if tcp.data:
                    messages = connection.streams[inbound].add(tcp.seq + int(bool(tcp.flags & dpkt.tcp.TH_SYN)), bytes(tcp.data))
                    for message in messages:
                        if inbound:
                            connection.requests.append(request_info(message))
                            continue
                        if 100 <= int(message.status) < 200:
                            continue
                        request = connection.requests.popleft() if connection.requests else None
                        if request is None or request['uri'] != '/v1/responses':
                            continue
                        if args.thread_id and request.get('client_metadata', {}).get('thread_id') != args.thread_id:
                            continue
                        samples += 1
                        record = save_exchange(out, samples, port, request, message)
                        print(f"[{samples:04d}] 请求={record['requested_effort']} 响应={record['response_effort']} "
                              f"差异={record['effort_differs']} 推理tokens={record['reasoning_tokens']} "
                              f"完成={record['response_completed']} 请求ID={record['upstream_request_id']}", flush=True)
                        if args.count and samples >= args.count:
                            break
                if tcp.flags & dpkt.tcp.TH_RST or (not inbound and tcp.flags & dpkt.tcp.TH_FIN):
                    unfinished_closed += int(connection.unfinished())
                    connections.pop(key, None)
            except (ValueError, dpkt.UnpackError) as exc:
                errors += 1
                print(f'连接 {port} 无法完整重组: {type(exc).__name__}；跳过该连接。', flush=True)
                connections.pop(key, None)
    except KeyboardInterrupt:
        print('\n已停止。', flush=True)
    except Exception:
        state = 'failed'
        raise
    finally:
        summary = {'state': state, 'samples': samples, 'packets': packets, 'errors': errors,
                   'unfinished_connections': unfinished_closed + sum(c.unfinished() for c in connections.values()),
                   'elapsed_seconds': round(time.monotonic() - start, 2), 'npcap': pcap.stats()}
        json_write(out / 'status.json', summary)
        print(f'已保存 {samples} 次完整 HTTP 交互，结果: {out / "summary.csv"}', flush=True)
    return 0 if samples else 2


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='抓取 Codex 每次请求强度与 Responses 响应中的强度值。')
    parser.add_argument('--port', type=int, default=18080)
    parser.add_argument('--seconds', type=float, default=300, help='持续秒数；0 为持续运行')
    parser.add_argument('--count', type=int, default=0, help='完整 HTTP 交互数量；0 不限制')
    parser.add_argument('--thread-id', help='仅保存 client_metadata.thread_id 精确相等的请求')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'captures')
    parser.add_argument('--npcap-dir', type=Path, default=Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'Npcap')
    parser.add_argument('--doctor', action='store_true', help='检查 Npcap 并尝试打开回环接口，然后退出')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.seconds < 0 or args.count < 0:
        parser.error('port 必须为 1..65535；seconds/count 不能为负数')
    pcap = None
    try:
        pcap = Npcap(args.npcap_dir)
        pcap.open()
        if args.doctor:
            print('OK: Npcap loopback capture is available. No configuration changed.')
            return 0
        return capture(args, pcap)
    except (OSError, RuntimeError) as exc:
        print(f'无法抓取: {exc}', file=sys.stderr)
        return 1
    finally:
        if pcap:
            pcap.close()


if __name__ == '__main__':
    raise SystemExit(main())
