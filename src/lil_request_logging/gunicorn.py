"""Gunicorn adapter; configure accesslog='-' and this logger class."""

import sys

from gunicorn.glogging import Logger

from . import emit


def completed(resp):
    """Whether Gunicorn finished writing the response, or None if unknown.

    Gunicorn's workers call access() from a finally around writing the body, so
    an exception in flight means writing stopped early: the client or a proxy
    went away, or the application raised partway through its body. Counting
    bytes cannot tell the same thing, because Gunicorn counts a chunk before
    writing it and does not count bytes sent with sendfile at all.

    An exception before the headers went out means nothing was sent yet, and
    Gunicorn writes an error response that gets its own record; so does the
    record it logs for that error page, which it writes before sending the page.
    Neither can say how the response ended. Responses that do not report
    headers_sent, such as those from Gunicorn's own ASGI worker, are treated the
    same way.
    """
    if sys.exc_info()[0] is None:
        return True
    return False if getattr(resp, "headers_sent", False) else None


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
            complete=completed(resp),
            trusted_peers=self.trusted_peers,
        )
