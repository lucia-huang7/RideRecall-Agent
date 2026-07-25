"""A loopback peer cannot use a foreign Host or browser Origin in demo mode."""
from functools import partial
import http.client
import importlib.util
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer

from fastapi.testclient import TestClient
from core.http_boundary import loopback_authority, loopback_origin
from mobility.app import create_app


def test_authority_and_origin_allow_only_explicit_loopback_addresses():
    for host in ('localhost','LOCALHOST:5175','127.0.0.1:8010','[::1]:8011','127.0.0.2'):
        assert loopback_authority(host),host
        assert loopback_origin('http://'+host)
    for host in ('','example.com','localhost.example.com','localhost@evil.example','evil.example@localhost','localhost:70000','localhost:0','localhost/path','127.0.0.1?x=1','localhost,evil.example',' localhost','[::1%lo0]:8010','localhost#x'):
        assert not loopback_authority(host),host
    assert loopback_origin(None)
    for origin in ('','null','file:///tmp/demo','https://evil.example','http://localhost/path','http://localhost:99999','http://localhost?x=1'):
        assert not loopback_origin(origin),origin


def test_backend_rejects_foreign_authorities_before_any_engine_work(tmp_path):
    with TestClient(create_app(tmp_path/'cases.sqlite3')) as client:
        before=client.app.state.service.engine.client.total_calls
        for headers in ({'Host':'evil.example'},{'Origin':'https://evil.example'},{'Origin':'null'},[('Host','localhost'),('Host','evil.example')],[('Origin','http://localhost'),('Origin','https://evil.example')]):
            response=client.get('/health',headers=headers)
            assert response.status_code==403 and 'loopback host and origin' in response.json()['detail']
        assert client.get('/health',headers={'Host':'127.0.0.1:8010','Origin':'http://127.0.0.1:5175'}).status_code==200
        assert client.get('/health',headers={'Host':'[::1]:8010','Origin':'http://localhost:8011'}).status_code==200
        assert client.app.state.service.engine.client.total_calls==before


def test_actual_static_proxy_refuses_foreign_host_before_forwarding(tmp_path):
    spec=importlib.util.spec_from_file_location('boundary_frontend',Path(__file__).resolve().parents[2]/'scripts/runtime/frontend.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'index.html').write_text('Local workspace')
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(module.Frontend,directory=str(tmp_path)))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def call(host,origin=None,duplicates=False):
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        try:
            connection.putrequest('GET','/',skip_host=True)
            connection.putheader('Host',host)
            if duplicates:connection.putheader('Host','localhost')
            if origin is not None:connection.putheader('Origin',origin)
            connection.endheaders();response=connection.getresponse();raw=response.read()
            return response.status,raw
        finally:connection.close()
    try:
        assert call('localhost')[0]==200
        assert call('127.0.0.1:5175','http://localhost:5175')==(200,b'Local workspace')
        assert call('evil.example')[0]==403
        assert call('localhost','https://evil.example')[0]==403
        assert call('localhost','null')[0]==403
        assert call('localhost',duplicates=True)[0]==403
    finally:server.shutdown();server.server_close();thread.join(timeout=3)
