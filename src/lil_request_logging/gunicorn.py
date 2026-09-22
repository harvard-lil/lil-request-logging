"""Gunicorn adapter; configure accesslog='-' and this logger class."""

from gunicorn.glogging import Logger

from . import emit


class AccessLogger(Logger):
    trusted_peers = ()

    def access(self, resp, req, environ, request_time):
        if not self.cfg.accesslog:
            return
        emit(
            method=environ.get("REQUEST_METHOD"),
            path=environ.get("PATH_INFO", ""),
            protocol=environ.get("SERVER_PROTOCOL"),
            peer_ip=environ.get("REMOTE_ADDR"),
            headers={key[5:].lower().replace("_", "-"): value
                     for key, value in environ.items() if key.startswith("HTTP_")},
            status=int(str(resp.status).split()[0]) if resp.status else None,
            response_bytes=resp.sent,
            duration_ms=request_time.total_seconds() * 1000,
            trusted_peers=self.trusted_peers,
        )
