"""Bounded, incremental access to the existing capture files."""
from collections import deque
from datetime import datetime
import json
from pathlib import Path
import time

from conversation_names import ConversationNames

ROW_FIELDS = ('sample', 'captured_at', 'thread_id', 'request_model', 'response_model',
              'requested_effort', 'response_effort', 'effort_differs', 'reasoning_tokens',
              'response_completed', 'http_status', 'upstream_request_id')


class CaptureStore:
    def __init__(self, root, names=None):
        self.root = Path(root).resolve()
        self.names = names or ConversationNames()
        self.session_cache, self.session_checked, self.run = [], -10.0, None
        self.reset()

    def reset(self):
        self.offset = 0
        self.rows = deque(maxlen=300)
        self.total = self.different = self.unknown = 0
        self.file_id, self.status = None, {}

    def sessions(self, force=False):
        if not force and time.monotonic() - self.session_checked < 5:
            return self.session_cache
        entries = []
        if self.root.exists():
            for item in self.root.iterdir():
                if item.is_dir() and not item.is_symlink():
                    stat = item.stat()
                    entries.append((stat.st_mtime, item.name, datetime.fromtimestamp(stat.st_ctime).astimezone().isoformat()))
        self.session_cache = [{'id': name, 'created_at': created} for _, name, created in sorted(entries, reverse=True)[:100]]
        self.session_checked = time.monotonic()
        return self.session_cache

    def directory(self, run):
        if not isinstance(run, str) or not run or run in ('.', '..') or any(c in run for c in '/\\\x00:'):
            raise ValueError('无效的记录目录。')
        folder = (self.root / run).resolve()
        if folder.parent != self.root or (self.root / run).is_symlink() or not folder.is_dir():
            raise ValueError('记录目录不存在。')
        return folder

    def read(self, run):
        folder = self.directory(run)
        if self.run != run:
            self.run = run
            self.reset()
        log = folder / 'records.jsonl'
        more = False
        if log.exists() and not log.is_symlink():
            stat = log.stat()
            identity = (stat.st_dev, stat.st_ino)
            if stat.st_size < self.offset or self.file_id not in (None, identity):
                self.reset()
            self.file_id = identity
            with log.open('rb') as handle:
                handle.seek(self.offset)
                data = handle.read(1024 * 1024)
            end = data.rfind(b'\n') + 1
            for line in data[:end].splitlines():
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        continue
                except (ValueError, UnicodeError):
                    continue
                self.total += 1
                self.different += record.get('effort_differs') is True
                self.unknown += record.get('effort_differs') is None
                self.rows.append({field: record.get(field) for field in ROW_FIELDS})
            self.offset += end
            more = end > 0 and self.offset < stat.st_size
        status_file = folder / 'status.json'
        if status_file.exists() and not status_file.is_symlink():
            try:
                self.status = json.loads(status_file.read_text(encoding='utf-8'))
            except (ValueError, UnicodeError, OSError):
                pass
        names = self.names.resolve(row.get('thread_id') for row in self.rows)
        rows = [dict(row, thread_name=names.get(row.get('thread_id'))) for row in reversed(self.rows)]
        return {'id': run, 'rows': rows, 'total': self.total,
                'different': self.different, 'unknown': self.unknown,
                'status': self.status, 'more': more, 'limit': self.rows.maxlen}

    def detail(self, run, sample):
        if type(sample) is not int or sample < 1:
            raise ValueError('无效的记录编号。')
        path = self.directory(run) / f'{sample:04d}.json'
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('记录不存在或无法读取。')
        record = json.loads(path.read_text(encoding='utf-8'))
        record['thread_name'] = self.names.resolve([record.get('thread_id')]).get(record.get('thread_id'))
        return record
