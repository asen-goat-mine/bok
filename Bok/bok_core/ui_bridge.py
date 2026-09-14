from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

from .api import MAX_REQUEST_BYTES, BokAPIServer, create_server
from .config import BokConfig


MAX_RESPONSE_BYTES = 8 * 1024 * 1024
PUBLIC_PREFIX = "/api/bok"
BROWSER_ROUTE_ALLOWLIST = {
    ("GET", "/v1/background"),
    ("POST", "/v1/background"),
    ("GET", "/v1/health"),
    ("GET", "/v1/today"),
    ("GET", "/v1/memory/inbox"),
    ("GET", "/v1/quick-notes"),
    ("GET", "/v1/activity"),
    ("GET", "/v1/versions"),
    ("GET", "/v1/backups"),
    ("GET", "/v1/documents/read"),
    ("GET", "/v1/person/dashboard"),
    ("GET", "/v1/person/claims/explain"),
    ("GET", "/v1/person/backups"),
    ("POST", "/v1/search"),
    ("POST", "/v1/quick-notes"),
    ("POST", "/v1/quick-notes/promote"),
    ("POST", "/v1/quick-notes/archive"),
    ("POST", "/v1/memory/commit"),
    ("POST", "/v1/memory/reject"),
    ("POST", "/v1/memory/rollback"),
    ("POST", "/v1/documents/write"),
    ("POST", "/v1/documents/rollback"),
    ("POST", "/v1/backups/create"),
    ("POST", "/v1/backups/verify"),
    ("POST", "/v1/backups/restore"),
    ("POST", "/v1/person/claims/confirm"),
    ("POST", "/v1/person/claims/authorize"),
    ("POST", "/v1/person/claims/correct"),
    ("POST", "/v1/person/claims/reject"),
    ("POST", "/v1/person/claims/forget"),
    ("POST", "/v1/person/observations/process"),
    ("POST", "/v1/person/outcomes"),
    ("POST", "/v1/person/cleanup"),
    ("POST", "/v1/person/backups/create"),
    ("POST", "/v1/person/backups/verify"),
    ("POST", "/v1/person/backups/restore"),
}


@dataclass(frozen=True)
class BokBridgeResponse:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"


class BokUIBridge:
    """Same-origin server-side bridge from the preview UI to the local Bok API.

    The browser never receives Bok's bearer token. The bridge delegates to the
    real HTTP API on an ephemeral loopback port, so API routing and business
    rules continue to have a single implementation.
    """

    def __init__(
        self,
        vault_root: Path,
        *,
        config_overrides: Optional[Mapping[str, object]] = None,
    ) -> None:
        self.vault_root = Path(vault_root).resolve()
        self.config_overrides = dict(config_overrides or {})
        self._lock = threading.RLock()
        self._server: Optional[BokAPIServer] = None
        self._thread: Optional[threading.Thread] = None
        self._base_url = ""
        self._opener = build_opener(ProxyHandler({}))

    @staticmethod
    def _json_response(status: int, code: str, message: str) -> BokBridgeResponse:
        body = json.dumps(
            {"error": {"code": code, "message": message}},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return BokBridgeResponse(status=status, body=body)

    def _ensure_started(self) -> None:
        with self._lock:
            if self._server is not None:
                return
            overrides = dict(self.config_overrides)
            overrides.update({"host": "127.0.0.1", "port": 0})
            config = BokConfig.load(self.vault_root, overrides)
            server = create_server(config)
            thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.25},
                name="bok-ui-bridge",
                daemon=True,
            )
            thread.start()
            host, port = server.server_address
            self._server = server
            self._thread = thread
            self._base_url = f"http://{host}:{port}"

    @staticmethod
    def _internal_target(public_target: str) -> str:
        parsed = urlsplit(public_target)
        if not parsed.path.startswith(PUBLIC_PREFIX + "/"):
            raise ValueError("Bok bridge path must start with /api/bok/")
        internal_path = parsed.path[len(PUBLIC_PREFIX) :]
        if not internal_path.startswith("/v1/"):
            raise ValueError("Only versioned Bok API routes are available")
        return urlunsplit(("", "", internal_path, parsed.query, ""))

    def forward(
        self,
        method: str,
        public_target: str,
        *,
        body: bytes = b"",
        headers: Optional[Mapping[str, str]] = None,
    ) -> BokBridgeResponse:
        normalized_method = str(method).upper()
        if normalized_method not in {"GET", "POST"}:
            return self._json_response(405, "method_not_allowed", "Use GET or POST")
        if len(body) > MAX_REQUEST_BYTES:
            return self._json_response(413, "request_too_large", "Request exceeds the 1 MiB limit")
        try:
            internal_target = self._internal_target(public_target)
        except ValueError as error:
            return self._json_response(404, "route_not_found", str(error))
        route = urlsplit(internal_target).path.rstrip("/")
        if (normalized_method, route) not in BROWSER_ROUTE_ALLOWLIST:
            return self._json_response(
                403,
                "browser_route_forbidden",
                "This route is not part of the explicit local browser capability set",
            )

        try:
            self._ensure_started()
            assert self._server is not None
            request_headers = {
                "Authorization": f"Bearer {self._server.token}",
                "Accept": "application/json",
            }
            incoming = headers or {}
            if incoming.get("Content-Type"):
                request_headers["Content-Type"] = str(incoming["Content-Type"])
            if incoming.get("Idempotency-Key"):
                request_headers["Idempotency-Key"] = str(incoming["Idempotency-Key"])
            request = Request(
                self._base_url + internal_target,
                data=body if normalized_method == "POST" else None,
                headers=request_headers,
                method=normalized_method,
            )
            try:
                with self._opener.open(request, timeout=30) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        return self._json_response(502, "response_too_large", "Bok response exceeded 8 MiB")
                    return BokBridgeResponse(
                        status=response.status,
                        body=raw,
                        content_type=response.headers.get("Content-Type", "application/json; charset=utf-8"),
                    )
            except HTTPError as error:
                raw = error.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    return self._json_response(502, "response_too_large", "Bok error response exceeded 8 MiB")
                return BokBridgeResponse(
                    status=error.code,
                    body=raw,
                    content_type=error.headers.get("Content-Type", "application/json; charset=utf-8"),
                )
        except (OSError, TimeoutError, URLError) as error:
            return self._json_response(503, "bok_unavailable", f"Bok is unavailable: {type(error).__name__}")
        except Exception:
            return self._json_response(500, "bridge_error", "The local Bok bridge encountered an internal error")

    def close(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
            self._base_url = ""
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
