"""ASGI adapter; disable the server's access log and proxy-header rewriting."""

from time import perf_counter

from . import emit


class AccessLog:
    def __init__(self, app, *, trusted_peers=()):
        self.app, self.trusted_peers = app, trusted_peers

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = perf_counter()
        status, size, complete = None, 0, False

        async def capture(message):
            nonlocal status, size, complete
            await send(message)
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                size += len(message.get("body", b""))
                complete = not message.get("more_body", False)

        try:
            await self.app(scope, receive, capture)
        finally:
            emit(
                method=scope["method"], path=scope["path"],
                protocol="HTTP/" + scope["http_version"],
                peer_ip=(scope.get("client") or (None,))[0],
                headers={k.decode("latin1").lower(): v.decode("latin1")
                         for k, v in scope.get("headers", [])},
                status=status, response_bytes=size, complete=complete,
                duration_ms=(perf_counter() - started) * 1000,
                trusted_peers=self.trusted_peers,
            )
