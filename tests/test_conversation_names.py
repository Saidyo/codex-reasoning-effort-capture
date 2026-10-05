import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from conversation_names import ConversationNames
from dashboard_store import CaptureStore


class ConversationNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = self.root / 'session_index.jsonl'
        self.names = ConversationNames(self.root, refresh_seconds=0)

    def append_name(self, id, name):
        with self.index.open('a', encoding='utf-8') as file:
            file.write(json.dumps({'id': id, 'thread_name': name}, ensure_ascii=False) + '\n')

    def test_new_conversation_and_rename_in_running_store(self):
        run = self.root / 'captures' / 'one'
        run.mkdir(parents=True)
        log = run / 'records.jsonl'
        log.write_text('{"sample":1,"thread_id":"first"}\n', encoding='utf-8')
        (run / '0001.json').write_text('{"sample":1,"thread_id":"first"}', encoding='utf-8')
        store = CaptureStore(run.parent, self.names)
        self.assertIsNone(store.read('one')['rows'][0]['thread_name'])
        self.append_name('first', '查看今天天气')
        self.assertEqual(store.read('one')['rows'][0]['thread_name'], '查看今天天气')
        self.append_name('second', '定位 B0 首屏耗时')
        with log.open('a', encoding='utf-8') as file:
            file.write('{"sample":2,"thread_id":"second"}\n')
        self.assertEqual([r['thread_name'] for r in store.read('one')['rows']], ['定位 B0 首屏耗时', '查看今天天气'])
        self.append_name('first', '重命名后的对话')
        self.assertEqual(store.read('one')['rows'][1]['thread_name'], '重命名后的对话')
        self.assertEqual(store.detail('one', 1)['thread_name'], '重命名后的对话')
        self.assertNotIn('thread_name', json.loads((run / '0001.json').read_text('utf-8')))

    def test_partial_line_and_index_replacement(self):
        self.index.write_bytes(b'{"id":"one","thread_name":')
        self.assertIsNone(self.names.resolve(['one'])['one'])
        with self.index.open('ab') as file:
            file.write(b'"first"}\n')
        self.assertEqual(self.names.resolve(['one'])['one'], 'first')
        replacement = self.root / 'new-index'
        replacement.write_text('{"id":"one","thread_name":"second"}\n', encoding='utf-8')
        replacement.replace(self.index)
        self.assertEqual(self.names.resolve(['one'])['one'], 'second')

    def test_database_name_precedes_index_and_refreshes_without_writes(self):
        self.append_name('one', '索引名称')
        db = self.root / 'state_5.sqlite'
        with closing(sqlite3.connect(db)) as c, c:
            c.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT)')
            c.execute('INSERT INTO threads VALUES (?, ?)', ('one', '界面名称'))
        before = db.read_bytes()
        self.assertEqual(self.names.resolve(['one'])['one'], '界面名称')
        self.assertEqual(db.read_bytes(), before)
        with closing(sqlite3.connect(db)) as c, c:
            c.execute('UPDATE threads SET name=? WHERE id=?', ('新名称', 'one'))
        self.assertEqual(self.names.resolve(['one'])['one'], '新名称')

    def test_missing_sources_do_not_create_database_or_guess_name(self):
        self.assertEqual(self.names.resolve(['unavailable']), {'unavailable': None})
        self.assertFalse((self.root / 'state_5.sqlite').exists())
        self.index.write_text('{"id":"one","title":"wrong field"}\n', encoding='utf-8')
        self.assertIsNone(self.names.resolve(['one'])['one'])

    def test_cached_poll_and_new_id_refresh(self):
        self.names.refresh_seconds = 3600
        self.append_name('one', '名称一')
        self.assertEqual(self.names.resolve(['one'])['one'], '名称一')
        self.append_name('one', '改名一')
        self.assertEqual(self.names.resolve(['one'])['one'], '名称一')
        self.append_name('two', '名称二')
        self.assertEqual(self.names.resolve(['one', 'two']), {'one': '改名一', 'two': '名称二'})


if __name__ == '__main__':
    unittest.main()
