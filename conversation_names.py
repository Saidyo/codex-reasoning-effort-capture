"""Read the exact Codex id/thread_name and threads.id/name fields locally."""
import json
import os
from pathlib import Path
import sqlite3
import time


class ConversationNames:
    def __init__(self, root=None, refresh_seconds=5):
        self.root = Path(root) if root is not None else Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
        self.refresh_seconds = refresh_seconds
        self.checked = -float('inf')
        self.index_names, self.names = {}, {}
        self.offset, self.identity = 0, None
        self.requested = set()

    def _read_index(self):
        path = self.root / 'session_index.jsonl'
        try:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if self.identity != identity or stat.st_size < self.offset:
                self.index_names.clear()
                self.offset = 0
            self.identity = identity
            with path.open('rb') as handle:
                handle.seek(self.offset)
                data = handle.read(1024 * 1024)
            end = data.rfind(b'\n') + 1
            for line in data[:end].splitlines():
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(record, dict) and isinstance(record.get('id'), str) and isinstance(record.get('thread_name'), str):
                    self.index_names[record['id']] = record['thread_name']
            self.offset += end
        except OSError:
            pass

    def resolve(self, thread_ids):
        ids = {value for value in thread_ids if isinstance(value, str) and value}
        now = time.monotonic()
        if now - self.checked >= self.refresh_seconds or not ids.issubset(self.requested):
            self._read_index()
            names = {key: self.index_names[key] for key in ids if self.index_names.get(key)}
            # This version and schema were inspected on this machine. Never scan
            # alternative database names or infer title fields. Unknown stays unknown.
            path = self.root / 'state_5.sqlite'
            try:
                connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.1)
                try:
                    if ids:
                        keys = list(ids)
                        for offset in range(0, len(keys), 300):
                            batch = keys[offset:offset + 300]
                            placeholders = ','.join('?' for _ in batch)
                            rows = connection.execute(f'SELECT id, name FROM threads WHERE id IN ({placeholders})', batch)
                            names.update({key: name for key, name in rows if isinstance(name, str) and name})
                finally:
                    connection.close()
            except (sqlite3.Error, OSError):
                pass
            self.names, self.requested, self.checked = names, ids, now
        return {key: self.names.get(key) for key in ids}
