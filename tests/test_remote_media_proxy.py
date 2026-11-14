import base64
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.parse import unquote, urlsplit

import pytest

from easel.remote_media_proxy import public_download_proxy


@pytest.mark.parametrize('method', ['CONNECT', 'GET'])
def test_subprocess_proxy_blocks_private_targets(method):
    contacted = threading.Event()
    class Victim(BaseHTTPRequestHandler):
        def do_GET(self):
            contacted.set()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'private-data')
        def log_message(self, *_args):
            return

    with ThreadingHTTPServer(('127.0.0.1', 0), Victim) as victim:
        thread = threading.Thread(target=victim.serve_forever, daemon=True)
        thread.start()
        try:
            target = f'127.0.0.1:{victim.server_port}'
            if method == 'GET':
                target = f'http://{target}/private'
            with public_download_proxy(max_bytes=1024, timeout=5) as url:
                proxy = urlsplit(url)
                authorization = base64.b64encode(f'{unquote(proxy.username)}:{unquote(proxy.password)}'.encode()).decode()
                connection = http.client.HTTPConnection(proxy.hostname, proxy.port, timeout=2)
                try:
                    connection.request(method, target, headers={'Proxy-Authorization': 'Basic '+authorization})
                    with pytest.raises(http.client.RemoteDisconnected):
                        connection.getresponse()
                finally:
                    connection.close()
        finally:
            victim.shutdown()
            thread.join(timeout=2)
    assert not contacted.is_set()


def test_subprocess_proxy_requires_its_ephemeral_credentials():
    with public_download_proxy(max_bytes=1024, timeout=5) as url:
        proxy = urlsplit(url)
        connection = http.client.HTTPConnection(proxy.hostname, proxy.port, timeout=2)
        try:
            connection.request('CONNECT', 'media.example:443')
            assert connection.getresponse().status == 407
        finally:
            connection.close()
