"""Loopback-only service. No dependencies beyond the existing Python runtime."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from local_window.store import SessionStore, ConflictError, atomic_json

STATIC = Path(__file__).with_name('static')
MAX_BODY = 10_000_000


def make_server(store, token, host='127.0.0.1', port=0):
    if host != '127.0.0.1':
        raise ValueError('本机编辑窗口只允许监听 127.0.0.1。')

    class Handler(BaseHTTPRequestHandler):
        server_version = 'TimingWindow/0.1'

        def log_message(self, fmt, *args):
            # Never log the launch fragment/token, request bodies or whole projects.
            print('%s %s' % (self.log_date_time_string(), fmt % args), flush=True)

        def _send(self, status, data, content_type='application/json; charset=utf-8'):
            if isinstance(data, (dict, list)):
                data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
            elif isinstance(data, str):
                data = data.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy',
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'none'")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _allowed(self, api=True):
            expected = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != expected:
                self._send(403, {'error': '请求不来自本机编辑地址。'})
                return False
            origin = self.headers.get('Origin')
            if origin is not None and origin != 'http://' + expected:
                self._send(403, {'error': '不允许其他页面访问此工程。'})
                return False
            supplied = self.headers.get('X-Timing-Token', '')
            if api and (not supplied.isascii() or not secrets.compare_digest(supplied, token)):
                self._send(403, {'error': '窗口连接已失效，请用启动入口重新打开。'})
                return False
            self.server.last_activity = time.monotonic()
            return True

        def do_GET(self):
            parsed = urlsplit(self.path)
            api = parsed.path.startswith('/api/')
            if not self._allowed(api):
                return
            try:
                query = parse_qs(parsed.query)
                if parsed.path == '/api/state':
                    state = store.state()
                    if query.get('since') == [str(state['revision'])]:
                        state = {'unchanged': True, 'revision': state['revision']}
                    self._send(200, state)
                elif parsed.path == '/api/relations':
                    self._send(200, store.relations(
                        query.get('threshold', ['200p'])[0],
                        query.get('signal_id', [None])[0], query.get('kind', ['all'])[0]))
                elif parsed.path == '/api/va':
                    self._send(200, store.va())
                elif parsed.path in ('/', '/index.html', '/app.js', '/app.css'):
                    filename = 'index.html' if parsed.path == '/' else parsed.path[1:]
                    kinds = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
                             '.css': 'text/css; charset=utf-8'}
                    path = STATIC / filename
                    if path.is_file():
                        self._send(200, path.read_bytes(), kinds[path.suffix])
                    else:
                        self._send(503, {'error': '窗口资源尚未就绪。'})
                elif parsed.path == '/favicon.ico':
                    self._send(204, b'', 'image/x-icon')
                else:
                    self._send(404, {'error': '没有此接口或文件。'})
            except (ValueError, TypeError, KeyError, RecursionError) as exc:
                self._send(400, {'error': str(exc)})
            except OSError:
                self._send(500, {'error': '读取工程失败，请查看本机运行日志。'})

        def do_POST(self):
            if not self._allowed(True):
                return
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                if not 0 <= length <= MAX_BODY:
                    self._send(413, {'error': '请求过大或缺少长度。'})
                    return
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    self._send(415, {'error': '需要 JSON 数据。'})
                    return
                def reject(value):
                    raise ValueError('JSON 不允许非有限数字。')
                data = json.loads(self.rfile.read(length).decode('utf-8'), parse_constant=reject)
                if not isinstance(data, dict):
                    raise ValueError('请求应为 JSON 对象。')
                path = urlsplit(self.path).path
                if path == '/api/edit':
                    result = store.edit(data.get('revision'), data.get('operation'))
                elif path == '/api/project':
                    result = store.replace(data.get('revision'), data.get('project'),
                                           data.get('label', '助手更新时序'))
                elif path == '/api/export':
                    result = store.export(data.get('revision'))
                elif path == '/api/shutdown':
                    result = {'stopping': True}
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self._send(404, {'error': '没有此接口。'})
                    return
                self._send(200, result)
            except ConflictError as exc:
                self._send(409, {'error': str(exc), 'state': exc.state})
            except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
                self._send(400, {'error': str(exc)})
            except OSError:
                self._send(500, {'error': '保存失败；当前操作尚未确认，请保留窗口并检查运行日志和磁盘空间。'})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.last_activity = time.monotonic()
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', required=True, type=Path)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--idle-seconds', type=int, default=1800)
    args = parser.parse_args()
    args.session.mkdir(parents=True, exist_ok=True)
    # Prevent two independently launched services from editing one session.
    session_lock = (args.session / 'service.lock').open('a+b')
    if session_lock.tell() == 0:
        session_lock.write(b'0')
        session_lock.flush()
    session_lock.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(session_lock.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(session_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    initial = None
    if args.project:
        import model
        initial = model.load_project(args.project.read_text(encoding='utf-8-sig'))
    store = SessionStore(args.session, initial)
    token = secrets.token_urlsafe(32)
    server = make_server(store, token)
    info = {'pid': os.getpid(), 'port': server.server_port, 'token': token,
            'session': str(store.directory), 'root': str(ROOT), 'version': '0.1-preview'}
    atomic_json(store.directory / 'connection.json', info)
    print(f'Timing window ready on loopback port {server.server_port}', flush=True)

    def idle_watch():
        while True:
            time.sleep(30)
            if args.idle_seconds > 0 and time.monotonic() - server.last_activity > args.idle_seconds:
                server.shutdown()
                return
    threading.Thread(target=idle_watch, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.3)
    finally:
        server.server_close()
        session_lock.close()


if __name__ == '__main__':
    main()
