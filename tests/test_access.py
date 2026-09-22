import asyncio
import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from types import SimpleNamespace

from gunicorn.config import Config
from lil_request_logging import clean_referrer, emit
from lil_request_logging.asgi import AccessLog
from lil_request_logging.gunicorn import AccessLogger


class AccessTests(unittest.TestCase):
    def test_privacy_and_trust(self):
        headers = {
            'referer': 'https://user:password@example.test:8443/path?secret=yes#secret',
            'user-agent': 'client\nsecond line', 'cookie': 'cookie-secret',
            'authorization': 'auth-secret', 'cf-connecting-ip': '192.0.2.1',
        }
        for peers, expected in [((), None), (('127.0.0.1',), '192.0.2.1')]:
            with redirect_stdout(io.StringIO()) as out:
                emit(method='GET', path='/path?query-secret', protocol='HTTP/1.1',
                     peer_ip='127.0.0.1', headers=headers, status=200,
                     response_bytes=4, duration_ms=1, trusted_peers=peers)
            self.assertEqual(len(out.getvalue().splitlines()), 1)
            row = json.loads(out.getvalue())
            self.assertEqual(row['client_ip'], expected)
            self.assertEqual(row['referrer'], 'https://example.test:8443/path')
            self.assertNotIn('secret', out.getvalue())
            self.assertNotIn('password', out.getvalue())
        self.assertIsNone(clean_referrer('https://example.test:bad/path'))
        self.assertIsNone(clean_referrer('https://[invalid'))

    def test_gunicorn_callback(self):
        cfg = Config()
        cfg.set('accesslog', '-')
        logger = AccessLogger(cfg)
        with redirect_stdout(io.StringIO()) as out:
            logger.access(SimpleNamespace(status='201 Created', sent=4), None,
                          {'REQUEST_METHOD': 'POST', 'PATH_INFO': '/scan',
                           'QUERY_STRING': 'secret', 'HTTP_COOKIE': 'secret'},
                          timedelta(milliseconds=12.5))
        row = json.loads(out.getvalue())
        self.assertEqual((row['status'], row['response_bytes'], row['duration_ms']),
                         (201, 4, 12.5))
        self.assertIsNone(row['complete'])
        self.assertNotIn('secret', out.getvalue())

    def test_asgi_complete_and_interrupted(self):
        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200})
            await send({'type': 'http.response.body', 'body': b'data'})

        for interrupted in (False, True):
            async def send(message):
                if interrupted and message['type'] == 'http.response.body':
                    raise ConnectionResetError('client disconnected')

            scope = dict(type='http', method='POST', path='/scan', http_version='1.1',
                         query_string=b'secret', client=('192.0.2.2', 1234))
            with redirect_stdout(io.StringIO()) as out:
                call = AccessLog(app)(scope, None, send)
                if interrupted:
                    with self.assertRaises(ConnectionResetError):
                        asyncio.run(call)
                else:
                    asyncio.run(call)
            row = json.loads(out.getvalue())
            self.assertEqual(row['status'], 200)
            self.assertEqual(row['complete'], not interrupted)
            self.assertEqual(row['response_bytes'], 0 if interrupted else 4)
            self.assertNotIn('secret', out.getvalue())
