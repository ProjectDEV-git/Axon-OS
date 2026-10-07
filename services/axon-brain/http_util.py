"""Small urllib helpers shared by the AI provider and OAuth modules."""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_MAX_ERROR_BODY = 300


class HTTPRequestError(RuntimeError):
    """An HTTP call failed; ``status`` is 0 for network errors."""

    def __init__(self, message: str, status: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _is_local_host(host: str) -> bool:
    if host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private or addr.is_link_local


def check_url(url: str, allow_local_http: bool = True) -> str:
    """Return *url* if it is safe to send credentials to, else raise ``ValueError``.

    HTTPS is required, except plain HTTP to loopback or private-network hosts
    (local Ollama, LM Studio, a LAN inference box) when *allow_local_http*.
    """
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if not host:
        raise ValueError(f"URL has no host: {url!r}")
    if parts.scheme == "https":
        return url
    if parts.scheme == "http" and allow_local_http and _is_local_host(host):
        return url
    raise ValueError(f"only https:// URLs are allowed for remote hosts: {url!r}")


def request(
    method: str,
    url: str,
    *,
    json_body: Any = None,
    form: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> Any:
    """Open an HTTP request and return the response object (a context manager).

    Raises:
        HTTPRequestError: on HTTP error status or network failure. The message
            carries the status and a truncated body, never request headers.
    """
    check_url(url)
    hdrs = {"Accept": "application/json", "User-Agent": "AxonOS-Brain/1.0"}
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        hdrs["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # nosec B310 - scheme checked above
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode(errors="replace")[:_MAX_ERROR_BODY]
        except Exception:
            body = ""
        raise HTTPRequestError(f"HTTP {e.code} from {_origin(url)}", e.code, body) from None
    except (urllib.error.URLError, OSError) as e:
        raise HTTPRequestError(f"cannot reach {_origin(url)}: {e}") from None


def request_json(method: str, url: str, **kwargs: Any) -> Any:
    """Like :func:`request` but return the decoded JSON body."""
    with request(method, url, **kwargs) as resp:
        return json.loads(resp.read().decode() or "null")


def _origin(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
