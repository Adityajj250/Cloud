"""Small trusted-LAN file sharing prototype. Python 3.10+, no packages."""
import argparse
import hmac
import json
from pathlib import Path
import secrets
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit, quote

ROOT = Path(__file__).resolve().parent
LIMIT = 100 * 1024 * 1024
LOCK = threading.Lock()

class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def reply(self, status, data, kind='application/json'):
        if not isinstance(data, bytes):
            data = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        if hmac.compare_digest(self.headers.get('X-Share-Key', ''), self.server.key):
            return True
        self.reply(401, {'error': 'Enter the connection key printed on your laptop.'})
        return False

    def file_path(self, name):
        if not name or name in ('.', '..') or any(c in name for c in '/\\:') or any(ord(c) < 32 for c in name):
            raise ValueError('Invalid filename')
        path = self.server.folder / name
        if path.is_symlink() or path.resolve().parent != self.server.folder:
            raise ValueError('Invalid filename')
        return path

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == '/':
            return self.reply(200, (ROOT / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        if not self.authorized():
            return
        if url.path == '/api/files':
            files = [{'name': p.name, 'size': p.stat().st_size} for p in self.server.folder.iterdir()
                     if p.is_file() and not p.is_symlink()]
            return self.reply(200, sorted(files, key=lambda f: f['name'].lower()))
        if url.path == '/api/download':
            try:
                path = self.file_path(parse_qs(url.query).get('name', [''])[0])
                with path.open('rb') as f:
                    size = path.stat().st_size
                    if size > LIMIT:
                        return self.reply(413, {'error': 'Prototype download limit: 100 MiB.'})
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(path.name, safe=''))
                    self.send_header('Content-Length', str(size))
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    while chunk := f.read(65536):
                        self.wfile.write(chunk)
                return
            except (ValueError, OSError):
                return self.reply(404, {'error': 'File unavailable.'})
        self.reply(404, {'error': 'Not found'})

    def do_POST(self):
        if not self.authorized():
            return
        url = urlsplit(self.path)
        if url.path != '/api/upload':
            return self.reply(404, {'error': 'Not found'})
        temporary = None
        try:
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('Chunked requests are unsupported')
            size = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= size <= LIMIT:
                return self.reply(413, {'error': 'Choose a file up to 100 MiB.'})
            path = self.file_path(parse_qs(url.query).get('name', [''])[0])
            with tempfile.NamedTemporaryFile(dir=self.server.staging, delete=False) as f:
                temporary = Path(f.name)
                remaining = size
                while remaining:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise ValueError('Upload interrupted')
                    f.write(chunk)
                    remaining -= len(chunk)
            with LOCK:
                if path.exists():
                    return self.reply(409, {'error': 'That filename already exists. Rename your file first.'})
                temporary.rename(path)
            self.reply(201, {'name': path.name})
        except (ValueError, OSError) as exc:
            self.reply(400, {'error': str(exc)})
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def log_message(self, fmt, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    folder = (ROOT / 'shared').resolve()
    folder.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(('0.0.0.0', args.port), Handler)
    server.folder = folder
    server.key = secrets.token_urlsafe(18)
    with tempfile.TemporaryDirectory(prefix='.uploads-', dir=ROOT) as staging:
        server.staging = staging
        print(f'\nLocal check: http://localhost:{args.port}')
        print(f'Phone: http://<laptop-Wi-Fi-IPv4>:{args.port}')
        print(f'Connection key: {server.key}')
        print(f'Shared folder: {folder}\nKeep this window open. Ctrl+C stops sharing.\n', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()

if __name__ == '__main__':
    main()
