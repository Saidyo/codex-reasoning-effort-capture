"""One capture thread in the UI process; no resident child processes."""
import os
from pathlib import Path
import threading
from types import SimpleNamespace

from capture import Npcap, capture


class CaptureController:
    def __init__(self, root, pcap_factory=Npcap, runner=capture):
        self.root = Path(root)
        self.pcap_factory, self.runner = pcap_factory, runner
        self.lock, self.thread = threading.Lock(), None
        self.stop_event = threading.Event()
        self.state, self.run, self.error = 'idle', None, None
        self.options = {'port': 18080, 'seconds': 0, 'thread_id': ''}

    def snapshot(self):
        with self.lock:
            return {'state': self.state, 'run': self.run, 'error': self.error, 'options': dict(self.options)}

    def start(self, options):
        port, seconds, thread_id = options.get('port', 18080), options.get('seconds', 0), options.get('thread_id', '')
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('抓取端口必须是 1 到 65535 的整数。')
        if type(seconds) is not int or not 0 <= seconds <= 86400:
            raise ValueError('持续时间必须是 0 到 86400 秒的整数。')
        if not isinstance(thread_id, str) or len(thread_id) > 256:
            raise ValueError('会话 ID 必须是长度不超过 256 的文本。')
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise RuntimeError('已有抓取正在运行，请先停止。')
            self.state, self.error, self.run = 'starting', None, None
            self.options = {'port': port, 'seconds': seconds, 'thread_id': thread_id}
            self.stop_event = threading.Event()
            args = SimpleNamespace(output=self.root, port=port, seconds=seconds, count=0,
                                   thread_id=thread_id or None,
                                   npcap_dir=Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'Npcap')
            self.thread = threading.Thread(target=self._run, args=(args,), daemon=True, name='capture')
            self.thread.start()

    def _started(self, directory):
        with self.lock:
            self.run = directory.name
            if self.state != 'stopping':
                self.state = 'capturing'

    def _run(self, args):
        pcap = None
        try:
            pcap = self.pcap_factory(args.npcap_dir)
            pcap.open()
            self.runner(args, pcap, stop_event=self.stop_event, on_start=self._started)
        except Exception as exc:
            with self.lock:
                self.state, self.error = 'failed', str(exc)
        finally:
            if pcap is not None:
                try:
                    pcap.close()
                except Exception as exc:
                    with self.lock:
                        self.state, self.error = 'failed', str(exc)
            with self.lock:
                if self.state != 'failed':
                    self.state = 'stopped'

    def stop(self):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                self.state = 'stopping'
                self.stop_event.set()

    def close(self):
        self.stop()
        if self.thread is not None:
            self.thread.join(timeout=5)
