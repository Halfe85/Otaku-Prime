# -*- coding: utf-8 -*-
"""Asynchronous network diagnostics for artwork rejected by a web browser."""
from __future__ import annotations

import errno
import socket
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from resources.lib.logging_config import get_logger


LOGGER = get_logger(__name__)


def _safe_url(raw_url):
    parsed = urlsplit(str(raw_url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("invalid artwork URL")
    return "{}://{}{}".format(parsed.scheme, parsed.netloc, parsed.path), parsed


def _failure_kind(reason):
    if isinstance(reason, socket.gaierror):
        return "dns_failure"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(reason, ssl.SSLError):
        return "tls_failure"
    if isinstance(reason, ConnectionRefusedError) or getattr(reason, "errno", None) == errno.ECONNREFUSED:
        return "connection_refused"
    if isinstance(reason, OSError):
        return "network_error"
    return "request_error"


class ArtworkDiagnosticProbe:
    """Run a small, bounded HEAD probe without delaying the browser request."""

    def __init__(self, timeout=6, resolver=None, opener=None, max_pending=32, ttl=300):
        self.timeout = max(1, int(timeout))
        self._resolve = resolver or socket.getaddrinfo
        self._open = opener or urlopen
        self._max_pending = max(1, int(max_pending))
        self._ttl = max(1, int(ttl))
        self._pending = []
        self._seen = {}
        self._active = False
        self._stopping = threading.Event()
        self._lock = threading.Lock()

    def schedule(self, raw_url, kind="artwork", title="unknown title"):
        if self._stopping.is_set():
            return False
        try:
            safe_url, parsed = _safe_url(raw_url)
        except ValueError:
            return False
        now = time.monotonic()
        with self._lock:
            previous = self._seen.get(safe_url)
            if previous is not None and now - previous < self._ttl:
                return False
            self._seen[safe_url] = now
            if len(self._pending) >= self._max_pending:
                LOGGER.warning(
                    "Artwork diagnostic queue is full; skipped host=%s url=%s",
                    parsed.hostname,
                    safe_url,
                )
                return False
            self._pending.append((safe_url, str(kind), str(title)))
            if self._active:
                return True
            self._active = True
        threading.Thread(
            target=self._run,
            name="OtakuPrimeArtworkDiagnostic",
            daemon=True,
        ).start()
        return True

    def stop(self):
        """Discard queued probes; an active network probe may finish silently."""
        self._stopping.set()
        with self._lock:
            self._pending.clear()

    def _run(self):
        while True:
            with self._lock:
                if self._stopping.is_set() or not self._pending:
                    self._active = False
                    return
                job = self._pending.pop(0)
            self._diagnose(*job)

    def _addresses(self, host, port):
        rows = self._resolve(host, port, type=socket.SOCK_STREAM)
        return sorted({str(row[4][0]) for row in rows if row[4]})

    def _diagnose(self, safe_url, kind, title):
        if self._stopping.is_set():
            return
        parsed = urlsplit(safe_url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        started = time.monotonic()
        try:
            addresses = self._addresses(parsed.hostname, port)
        except socket.gaierror as exc:
            if self._stopping.is_set():
                return
            LOGGER.warning(
                "Artwork network diagnostic failed: result=dns_failure kind=%s "
                "title=%s host=%s error=%s url=%s",
                kind,
                title,
                parsed.hostname,
                exc,
                safe_url,
            )
            return
        LOGGER.info(
            "Artwork network diagnostic started: kind=%s title=%s host=%s "
            "dns=%s url=%s",
            kind,
            title,
            parsed.hostname,
            ",".join(addresses) or "none",
            safe_url,
        )
        request = Request(
            safe_url,
            headers={"Accept": "image/*", "User-Agent": "Otaku-Prime/0.1.2 artwork-diagnostic"},
            method="HEAD",
        )
        try:
            with self._open(request, timeout=self.timeout) as response:
                final_url, final_parsed = _safe_url(response.geturl())
                status = int(getattr(response, "status", 200))
                content_type = str(response.headers.get("Content-Type") or "unknown")
                content_length = str(response.headers.get("Content-Length") or "unknown")
        except HTTPError as exc:
            if self._stopping.is_set():
                return
            LOGGER.warning(
                "Artwork network diagnostic failed: result=http_error status=%s "
                "kind=%s title=%s host=%s dns=%s duration=%.2fs url=%s",
                exc.code,
                kind,
                title,
                parsed.hostname,
                ",".join(addresses) or "none",
                time.monotonic() - started,
                safe_url,
            )
            return
        except URLError as exc:
            if self._stopping.is_set():
                return
            reason = exc.reason
            LOGGER.warning(
                "Artwork network diagnostic failed: result=%s kind=%s title=%s "
                "host=%s dns=%s duration=%.2fs error=%s url=%s",
                _failure_kind(reason),
                kind,
                title,
                parsed.hostname,
                ",".join(addresses) or "none",
                time.monotonic() - started,
                reason,
                safe_url,
            )
            return
        except (OSError, TimeoutError, ssl.SSLError) as exc:
            if self._stopping.is_set():
                return
            LOGGER.warning(
                "Artwork network diagnostic failed: result=%s kind=%s title=%s "
                "host=%s dns=%s duration=%.2fs error=%s url=%s",
                _failure_kind(exc),
                kind,
                title,
                parsed.hostname,
                ",".join(addresses) or "none",
                time.monotonic() - started,
                exc,
                safe_url,
            )
            return
        if self._stopping.is_set():
            return
        LOGGER.info(
            "Artwork network diagnostic succeeded: status=%s kind=%s title=%s "
            "host=%s dns=%s final_host=%s content_type=%s content_length=%s "
            "duration=%.2fs url=%s final_url=%s",
            status,
            kind,
            title,
            parsed.hostname,
            ",".join(addresses) or "none",
            final_parsed.hostname,
            content_type,
            content_length,
            time.monotonic() - started,
            safe_url,
            final_url,
        )
