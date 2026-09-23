"""The Gunicorn adapter's `complete` against a real server.

`complete` depends on Gunicorn calling access() from a finally around writing
the body, with any exception still in flight. That is Gunicorn's behaviour, not
this package's, so it is checked by running Gunicorn with each worker class the
package's users run.
"""

import http.client
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

APP = '''
import os

CHUNK = b"x" * 65536

def application(environ, start_response):
    path = environ["PATH_INFO"]
    if path == "/file":
        start_response("200 OK", [("Content-Length", str(os.path.getsize(FILE)))])
        return environ["wsgi.file_wrapper"](open(FILE, "rb"))
    if path == "/raise":
        start_response("200 OK", [("Content-Length", str(len(CHUNK) * 4))])
        def body():
            yield CHUNK
            raise RuntimeError("the application failed partway through its body")
        return body()
    if path == "/big":
        start_response("200 OK", [("Content-Length", str(len(CHUNK) * 1024))])
        return (CHUNK for _ in range(1024))
    start_response("200 OK", [("Content-Length", "2")])
    return [b"ok"]
'''


class GunicornServerTests(unittest.TestCase):
    def serve(self, worker_class):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        served = tmp / "served.bin"
        served.write_bytes(b"f" * 200_000)
        (tmp / "app.py").write_text(f"FILE = {str(served)!r}\n" + APP)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        log = tmp / "stdout.log"
        with open(log, "w") as out:
            process = subprocess.Popen(
                [sys.executable, "-m", "gunicorn", "--bind", f"127.0.0.1:{port}",
                 "--worker-class", worker_class, "--workers", "1",
                 "--access-logfile", "-",
                 "--logger-class", "lil_request_logging.gunicorn.AccessLogger",
                 "app:application"],
                cwd=tmp, stdout=out, stderr=subprocess.STDOUT)
        self.addCleanup(process.kill)
        deadline = time.monotonic() + 15
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                if time.monotonic() > deadline or process.poll() is not None:
                    self.fail(f"gunicorn did not start:\n{log.read_text()}")
                time.sleep(0.1)
        return port, log

    def records(self, log, expected):
        deadline = time.monotonic() + 15
        while True:
            rows = {}
            for line in log.read_text().splitlines():
                if line.startswith("{") and '"http_access"' in line:
                    row = json.loads(line)
                    rows[row["path"]] = row
            if expected <= rows.keys() or time.monotonic() > deadline:
                return rows
            time.sleep(0.1)

    def test_complete_reports_how_the_body_ended(self):
        for worker_class in ("sync", "gthread"):
            with self.subTest(worker_class=worker_class):
                port, log = self.serve(worker_class)

                def get(path):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                    conn.request("GET", path)
                    return conn, conn.getresponse()

                conn, response = get("/ok")
                self.assertEqual(response.read(), b"ok")
                conn.close()

                conn, response = get("/file")
                self.assertEqual(len(response.read()), 200_000)
                conn.close()

                conn, response = get("/raise")
                with self.assertRaises(http.client.IncompleteRead):
                    response.read()
                conn.close()

                # A client that stops reading and goes away, as a proxy does when
                # its own timeout fires: the server's writes start failing.
                with socket.create_connection(("127.0.0.1", port), timeout=10) as client:
                    client.sendall(b"GET /big HTTP/1.1\r\nHost: test\r\n\r\n")
                    client.recv(65536)

                rows = self.records(log, {"/ok", "/file", "/raise", "/big"})
                self.assertIs(rows["/ok"]["complete"], True)
                # Served with sendfile, whose bytes Gunicorn does not count.
                self.assertIs(rows["/file"]["complete"], True)
                self.assertIs(rows["/raise"]["complete"], False)
                self.assertIs(rows["/big"]["complete"], False)
                self.assertLess(rows["/big"]["response_bytes"], 65536 * 1024)


if __name__ == "__main__":
    unittest.main()
