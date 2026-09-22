"""One JSON access record per request, written to stdout."""

import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit


def clean_referrer(value):
    try:
        url = urlsplit(value)
        host, port = url.hostname, url.port
    except ValueError:  # Malformed URL or port supplied by the client.
        return None
    if url.scheme not in ("http", "https") or not host:
        return None
    host = f"[{host}]" if ":" in host else host
    authority = f"{host}:{port}" if port is not None else host
    return f"{url.scheme}://{authority}{url.path}"


def emit(*, method, path, protocol, peer_ip, headers, status, response_bytes,
         duration_ms, complete=None, trusted_peers=()):
    """Headers use lowercase names; trust must be configured by the service."""
    record = {
        "schema_version": 1,
        "event": "http_access",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": os.getenv("SERVICE_NAME") or None,
        "environment": os.getenv("ENVIRONMENT") or None,
        "release": os.getenv("SENTRY_RELEASE") or None,
        "pid": os.getpid(),
        "method": method,
        "path": path.split("?", 1)[0],
        "protocol": protocol,
        "peer_ip": peer_ip,
        "client_ip": headers.get("cf-connecting-ip") if peer_ip in trusted_peers else None,
        "forwarded_for_untrusted": headers.get("x-forwarded-for"),
        "host": headers.get("host"),
        "referrer": clean_referrer(headers.get("referer", "")),
        "user_agent": headers.get("user-agent"),
        "cf_ray": headers.get("cf-ray"),
        "status": status,
        "response_bytes": response_bytes,
        "duration_ms": round(duration_ms, 3),
        "complete": complete,
    }
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stdout.flush()
