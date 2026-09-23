# lil-request-logging

Shared HTTP access logging for Library Innovation Lab services. The package writes
one JSON record to stdout for each request, using the same fields for Gunicorn
and ASGI applications such as FastAPI. ECS can send those records to CloudWatch
through its existing log driver, and Grafana can query them without a different
parser for each service.

The package handles access records. Application messages, exceptions, and server
lifecycle logs continue through their existing logging configuration.

## Installation

Releases include a Python wheel on GitHub. Add a versioned release to a service
and commit its updated dependency manifest and lockfile:

```sh
uv add 'lil-request-logging[gunicorn] @ https://github.com/harvard-lil/lil-request-logging/releases/download/v0.1.0/lil_request_logging-0.1.0-py3-none-any.whl'
```

For an ASGI application, omit `[gunicorn]`. The ASGI adapter has no dependencies
outside the Python standard library. Python 3.11 or newer is required.

## Gunicorn services

Select the logger in the application's Gunicorn configuration:

```python
from lil_request_logging.gunicorn import AccessLogger

logger_class = AccessLogger
accesslog = "-"
```

The logger writes JSON directly to stdout. It replaces Gunicorn's access-record
formatting, so `access_log_format` and `--access-logformat` are unnecessary.
Gunicorn's error logging configuration continues to apply to server errors.

### Client addresses behind Cloudflare Tunnel

The network peer seen by a service behind a tunnel is the tunnel process, rather
than the visitor. Cloudflare supplies the visitor address in `CF-Connecting-IP`.
The logger records that header as `client_ip` only when the connection came from
an address the service explicitly trusts.

For an ECS task whose Cloudflare Tunnel sidecar connects over loopback, configure:

```python
from lil_request_logging.gunicorn import AccessLogger

class TunnelAccessLogger(AccessLogger):
    trusted_peers = ("127.0.0.1", "::1")

logger_class = TunnelAccessLogger
accesslog = "-"
```

This assumes the task has no other inbound access and Cloudflare controls the
header. Review that assumption when changing ingress. `trusted_peers` contains
exact addresses, not CIDR ranges, and defaults to empty. It affects logging only;
it does not change Gunicorn or Django's proxy-header configuration.

## ASGI applications

Wrap the application after it has been constructed, outside its exception-handling
middleware. This lets the logger observe error responses produced by the framework:

```python
from fastapi import FastAPI
from lil_request_logging.asgi import AccessLog

api = FastAPI()
# Register routes and middleware on api.
app = AccessLog(api)
```

Start Uvicorn with `--no-access-log --no-proxy-headers`. Disabling the built-in
access log avoids duplicate records. Disabling proxy-header rewriting preserves
the transport peer address in the ASGI scope.

A service behind a private ALB can leave `trusted_peers` empty. Its records show
the ALB peer address and the untrusted forwarded chain. The adapter does not infer
a visitor address from that chain. For a service behind a trusted Cloudflare
sidecar, pass `trusted_peers=("127.0.0.1", "::1")` to `AccessLog` under the same
network assumptions described above.

## Fields and attribution

Every record has `event: "http_access"` so queries can distinguish access records
from other messages. `schema_version: 1` identifies the field definitions below.
Missing values are represented by JSON `null`.

| Fields | Meaning |
| --- | --- |
| `timestamp` | UTC time when the record is written, in ISO 8601 format |
| `service`, `environment`, `release` | Values of `SERVICE_NAME`, `ENVIRONMENT`, and `SENTRY_RELEASE` |
| `pid` | Process that wrote the record |
| `method`, `path`, `protocol` | Request method, decoded path without query string, and HTTP protocol |
| `status` | Observed HTTP status, or null if no response status was observed |
| `response_bytes` | Response body bytes reported by Gunicorn or accepted by ASGI `send`; excludes headers |
| `duration_ms` | Request duration in milliseconds |
| `complete` | Whether the server finished sending the response body: for ASGI, whether `send` accepted the final body message; for Gunicorn, whether the body was written without an exception. Null when that cannot be known |
| `peer_ip`, `client_ip` | Transport peer and, when trusted, Cloudflare's visitor address |
| `forwarded_for_untrusted` | Raw `X-Forwarded-For` value for diagnosis |
| `host`, `referrer`, `user_agent`, `cf_ray` | Selected request headers; referrer is sanitized |

Set the attribution variables when starting the service. Use the same environment
and release identifier as Sentry so logs and error events can be compared. A release
should identify the built artifact and remain unchanged when promoting it between
environments. The package does not initialize or configure Sentry.

Request duration and byte counts describe what the server observed; they do not
prove the client received the response. An interrupted request keeps its observed
status and has `complete: false`. A disconnect does not automatically become a
500. Exceptions continue to the server's existing error handling.

Under Gunicorn, `complete` is `false` when the response had started and writing
stopped early: the client or a proxy in front went away, or the application
raised partway through its body. It is null when an exception came before
anything was sent, since Gunicorn then writes an error response with its own
record, and for the record of that error response, which Gunicorn logs before
sending it. It does not compare `response_bytes` with `Content-Length`: Gunicorn
counts a chunk before writing it, and does not count bytes sent with `sendfile`,
so `response_bytes` can be 0 for a complete response served through
`wsgi.file_wrapper`.

## Privacy

Records exclude request query strings, cookies, authorization headers, bodies,
and headers outside the explicit field list. Referrers lose credentials, query
strings, and fragments. JSON encoding escapes newlines in header values so a
header cannot create another physical log line.

Paths, IP addresses, and allowed header values may still contain sensitive
information. Configure log access and retention accordingly. Forwarded addresses
and Ray IDs are diagnostic data, not authentication inputs.

## Dashboards and upgrades

Before changing a service from text logs to JSON, update its dashboard queries to
accept both formats. Parse legacy records into the corresponding fields and units,
then combine those values with JSON fields. This allows charts to span a rollout
and keeps mixed application versions queryable.

Keep legacy parsing through the log-retention and rollback windows. A field absent
from older records, such as request duration, stays unavailable for that period;
missing measurements should not be presented as zero. Each request should produce
one access record, rather than emitting both old and new formats.

## Development and releases

```sh
uv run --extra gunicorn python -m unittest discover -s tests
uv build
```

The tests cover both adapters, privacy filtering, proxy trust, and interrupted
responses. CI runs them against supported Python and Gunicorn versions.

For a release, update the package version and lockfile, run the tests, and build
the wheel and source distribution. Tag the tested commit as `v<version>` and attach
the built distributions to its GitHub release. Services pin a release in their
lockfiles and upgrade independently. Release assets should not be replaced;
publish a new version for corrections.
