"""Loopback-only static frontend with a same-origin API proxy."""
import json
import os
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade', 'host'}


class Frontend(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/__kinderuber_health':
            body = json.dumps({'service':'kinderuber-frontend', 'instance':os.environ['KINDER_UBER_INSTANCE_ID']}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith('/ride-api/'):
            self.proxy()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith('/ride-api/'):
            self.proxy()
        else:
            self.send_error(405, 'Only API requests accept POST')

    def proxy(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length < 0 or length > 4 * 1024 * 1024:
            self.send_error(413, 'Request is too large')
            return
        body = self.rfile.read(length) if length else None
        headers = {k:v for k,v in self.headers.items() if k.lower() not in HOP_HEADERS and k.lower() != 'content-length'}
        request = urllib.request.Request('http://127.0.0.1:8010'+self.path[len('/ride-api'):], data=body, headers=headers, method=self.command)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                upstream = opener.open(request, timeout=100)
            except urllib.error.HTTPError as error:
                upstream = error
            with upstream:
                data = upstream.read()
                self.send_response(upstream.status)
                for key,value in upstream.headers.items():
                    if key.lower() not in HOP_HEADERS and key.lower() != 'content-length':
                        self.send_header(key,value)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
        except (OSError, urllib.error.URLError):
            data = b'{"detail":"The backend is restarting. Please retry in a few seconds."}'
            self.send_response(503)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)


if __name__ == '__main__':
    directory = Path(__file__).resolve().parents[1] / 'app' / 'frontend' / 'dist'
    server = ThreadingHTTPServer(('127.0.0.1',5175),partial(Frontend,directory=str(directory)))
    server.daemon_threads = True
    server.serve_forever()
