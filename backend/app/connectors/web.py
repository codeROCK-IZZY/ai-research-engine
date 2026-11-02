"""Fetch a single web page safely (no crawling).

Server-side request forgery (SSRF) is the main risk when a server fetches URLs that users
supply: an attacker could make it read internal services (``http://169.254.169.254`` cloud
metadata, ``localhost`` admin ports, private network hosts). Defences, all enforced here:

* **HTTPS only**, standard port 443 only, no credentials in the URL.
* Optional **domain allow-list** (``RKE_URL_ALLOWLIST``): only listed domains and their
  sub-domains may be fetched.
* The hostname is **resolved once and every address checked**: private, loopback,
  link-local, multicast, reserved and unspecified addresses are refused.
* The connection is **pinned to the checked IP** (TLS still verifies the real hostname), so a
  DNS answer that changes between check and connect ("DNS rebinding") cannot redirect it.
* **Redirects are followed manually** (max 3) and each target goes through the same checks.
* **Limits:** 15 s timeout, 10 MB body, and only HTML, plain text, Markdown or PDF responses.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from app.core.config import get_settings
from app.core.errors import UnprocessableData

ALLOWED_TYPES = ("text/html", "text/plain", "text/markdown", "application/pdf", "application/xhtml+xml")
MAX_REDIRECTS = 3


@dataclass(frozen=True)
class Fetched:
    url: str
    content: bytes
    content_type: str
    last_modified: datetime | None


def _check_host(host: str) -> str:
    """Resolve ``host`` and return one safe IP address, or raise."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnprocessableData(f"Cannot resolve host {host}") from exc
    addresses = {str(info[4][0]) for info in infos}
    for a in addresses:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise UnprocessableData("This address is not allowed (private, internal or reserved network)")
    return sorted(addresses)[0]


def validate_url(url: str) -> tuple[str, str, str]:
    """Returns (host, path_with_query, pinned_ip)."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UnprocessableData("Only https:// URLs can be added")
    if parts.username or parts.password:
        raise UnprocessableData("URLs with credentials are not allowed")
    if parts.port not in (None, 443):
        raise UnprocessableData("Only the standard HTTPS port is allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnprocessableData("URL has no host")
    allow = [d.lower().lstrip(".") for d in get_settings().url_allowlist]
    if allow and not any(host == d or host.endswith("." + d) for d in allow):
        raise UnprocessableData(f"{host} is not on this deployment's allow-list")
    try:
        ipaddress.ip_address(host)
        raise UnprocessableData("Use a hostname, not an IP address")
    except ValueError:
        pass
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return host, path, _check_host(host)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection to a pre-validated IP, with SNI and certificate checks for the hostname."""

    def __init__(self, host: str, ip: str, timeout: float):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]


def fetch(url: str) -> Fetched:
    s = get_settings()
    limit = s.url_max_mb * 1024 * 1024
    for _ in range(MAX_REDIRECTS + 1):
        host, path, ip = validate_url(url)
        conn = _PinnedHTTPSConnection(host, ip, timeout=s.url_timeout_seconds)
        try:
            conn.request(
                "GET",
                path,
                headers={"User-Agent": "ResearchEngine/1.0 (+knowledge ingestion)", "Accept": ", ".join(ALLOWED_TYPES)},
            )
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.getheader("Location")
                if not loc:
                    raise UnprocessableData("Redirect without a location")
                url = urljoin(url, loc)
                continue
            if resp.status != 200:
                raise UnprocessableData(f"The page returned HTTP {resp.status}")
            ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if ctype not in ALLOWED_TYPES:
                raise UnprocessableData(f"Unsupported content type {ctype or 'unknown'}")
            body = resp.read(limit + 1)
            if len(body) > limit:
                raise UnprocessableData(f"The page is larger than {s.url_max_mb} MB")
            lm = resp.getheader("Last-Modified")
            modified = None
            if lm:
                try:
                    modified = parsedate_to_datetime(lm)
                except (TypeError, ValueError):
                    modified = None
            return Fetched(url=url, content=body, content_type=ctype, last_modified=modified)
        except (OSError, http.client.HTTPException) as exc:
            raise UnprocessableData(f"Could not fetch the page: {exc.__class__.__name__}") from exc
        finally:
            conn.close()
    raise UnprocessableData("Too many redirects")
