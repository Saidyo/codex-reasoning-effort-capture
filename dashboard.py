"""Small local dashboard: standard-library HTTP server + one capture thread."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

from dashboard_controller import CaptureController
from dashboard_store import CaptureStore

ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8')}


class DashboardServer(HTTPServer):
    def __init__(self, address, root, assets, capture_port=18080, controller=None):
        super().__init__(address, Handler)
        self.token = secrets.token_urlsafe(32)
        self.origin = f'http://127.0.0.1:{self.server_port}'
        self.store = CaptureStore(root)
        self.controller = controller or CaptureController(root)
        self.capture_port = capture_port
        self.assets = {route: ((assets / name).read_bytes(), mime) for route, (name, mime) in ASSETS.items()}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_):
        pass

    def respond(self, code, data, mime='application/json; charset=utf-8'):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)

    def allowed(self, query, api=False):
        origin = self.headers.get('Origin')
        if self.headers.get('Host') != self.server.origin[7:] or origin not in (None, self.server.origin):
            self.respond(403, {'error': '只允许从本机界面访问。'})
            return False
        if api:
            token = self.headers.get('X-AnyGPT-Token')
            if self.command == 'GET' and urlsplit(self.path).path == '/api/export':
                token = query.get('token', [''])[0]
            if not token or not secrets.compare_digest(token, self.server.token):
                self.respond(403, {'error': '界面会话已失效，请刷新页面。'})
                return False
        return True

    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if not self.allowed(query, url.path.startswith('/api/')):
            return
        try:
            if url.path in self.server.assets:
                data, mime = self.server.assets[url.path]
                if url.path == '/':
                    data = data.replace(b'__TOKEN__', self.server.token.encode())
                self.respond(200, data, mime)
            elif url.path == '/api/state':
                control = self.server.controller.snapshot()
                sessions = self.server.store.sessions()
                run = query.get('run', [None])[0] or control['run'] or (sessions[0]['id'] if sessions else None)
                selected = self.server.store.read(run) if run else None
                self.respond(200, {'capture': control, 'sessions': sessions, 'selected': selected,
                                   'default_port': self.server.capture_port})
            elif url.path == '/api/record':
                self.respond(200, self.server.store.detail(query.get('run', [''])[0], int(query.get('sample', ['0'])[0])))
            elif url.path == '/api/export':
                path = self.server.store.directory(query.get('run', [''])[0]) / 'summary.csv'
                if not path.is_file() or path.is_symlink():
                    raise ValueError('当前记录还没有可导出的 CSV。')
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv; charset=utf-8')
                self.send_header('Content-Disposition', 'attachment; filename="summary.csv"')
                self.send_header('Content-Length', str(path.stat().st_size))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                with path.open('rb') as handle:
                    shutil.copyfileobj(handle, self.wfile, length=65536)
            else:
                self.respond(404, {'error': '页面不存在。'})
        except (ValueError, OSError) as exc:
            self.respond(400, {'error': str(exc)})

    def do_POST(self):
        if not self.allowed({}, True):
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 4096:
                raise ValueError('请求内容过长。')
            data = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(data, dict):
                raise ValueError('请求必须是 JSON 对象。')
            if self.path == '/api/start':
                self.server.controller.start(data)
            elif self.path == '/api/stop':
                self.server.controller.stop()
            elif self.path == '/api/quit':
                self.server.controller.stop()
                self.respond(200, {'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                self.respond(404, {'error': '操作不存在。'})
                return
            self.respond(200, {'ok': True})
        except (ValueError, UnicodeError) as exc:
            self.respond(400, {'error': str(exc)})
        except RuntimeError as exc:
            self.respond(409, {'error': str(exc)})


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description='本机思考强度可视化界面')
    parser.add_argument('--port', type=int, default=0, help='界面端口；0 自动选择空闲端口')
    parser.add_argument('--capture-port', type=int, default=18080)
    parser.add_argument('--output', type=Path, default=base / 'captures')
    parser.add_argument('--open', action='store_true', help='在默认浏览器打开界面')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535 or not 1 <= args.capture_port <= 65535:
        parser.error('端口无效')
    server = DashboardServer(('127.0.0.1', args.port), args.output, base / 'web', args.capture_port)
    print(f'界面地址: {server.origin}', flush=True)
    print('在页面点“退出界面”或在此窗口按 Ctrl+C，结束整个服务。', flush=True)
    (base / 'ui-state.json').write_text(json.dumps({'url': server.origin, 'pid': os.getpid()}), encoding='utf-8')
    try:
        if args.open:
            webbrowser.open(server.origin)
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.controller.close()
        server.server_close()
        print('界面服务已退出。', flush=True)


if __name__ == '__main__':
    main()
