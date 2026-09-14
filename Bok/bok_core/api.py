from __future__ import annotations

import hmac
import ipaddress
import json
import threading
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from .config import BokConfig
from .errors import BokError, ConflictError
from .service import BokService
from .util import InterProcessFileLock, atomic_write_json, canonical_json, read_json, sha256_text, utc_now


MAX_REQUEST_BYTES = 1024 * 1024


class IdempotencyStore:
    def __init__(self, service: BokService):
        self.path = service.config.state_dir / "state" / "idempotency.json"
        self.lock = InterProcessFileLock(service.config.state_dir / "write.lock")

    def run(self, key: str, fingerprint: str, callback: Callable[[], dict]) -> dict:
        if not key:
            return callback()
        if len(key) > 160:
            raise BokError("invalid_idempotency_key", "Idempotency key is too long")
        with self.lock:
            state = read_json(self.path, {})
            if not isinstance(state, dict):
                state = {}
            existing = state.get(key)
            if existing:
                if existing.get("fingerprint") != fingerprint:
                    raise ConflictError("Idempotency key was reused with a different request")
                response = dict(existing.get("response") or {})
                response["idempotent_replay"] = True
                return response
            response = callback()
            # The callback may intentionally scrub older receipts while holding
            # this re-entrant process lock. Reload before writing the new result
            # so stale in-memory state cannot resurrect forgotten content.
            latest = read_json(self.path, {})
            if isinstance(latest, dict):
                state = latest
            state[key] = {"fingerprint": fingerprint, "response": response, "created_at": utc_now()}
            if len(state) > 1000:
                state = dict(list(state.items())[-1000:])
            atomic_write_json(self.path, state)
            return response


class BokAPIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, service: BokService):
        self.service = service
        self.token = service.auth_token()
        self.idempotency = IdempotencyStore(service)
        self.started_at = time.time()
        self.capture_stop = threading.Event()
        self.capture_thread: Optional[threading.Thread] = None
        self.rate_lock = threading.Lock()
        self.rate_events = defaultdict(deque)
        super().__init__(address, BokAPIHandler)

    def allow_request(self, client: str, limit: int = 240, window: float = 60.0) -> bool:
        now = time.monotonic()
        with self.rate_lock:
            events = self.rate_events[client]
            while events and now - events[0] > window:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True

    def start_capture_worker(self) -> None:
        if self.capture_thread and self.capture_thread.is_alive():
            return

        def worker() -> None:
            while not self.capture_stop.is_set():
                interval = 30
                try:
                    # Bound each queue and wait between rounds. Receipts and
                    # explicitly requested operations remain available while
                    # the automatic worker is paused.
                    settings = self.service.process_background()
                    interval = settings["interval_seconds"]
                except Exception:
                    pass
                self.capture_stop.wait(interval)

        self.capture_thread = threading.Thread(target=worker, name="bok-memory-worker", daemon=True)
        self.capture_thread.start()

    def server_close(self) -> None:
        self.capture_stop.set()
        if self.capture_thread and self.capture_thread is not threading.current_thread():
            self.capture_thread.join(timeout=3)
        super().server_close()


class BokAPIHandler(BaseHTTPRequestHandler):
    server: BokAPIServer

    def log_message(self, _format: str, *_args) -> None:
        return

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        super().end_headers()

    def _json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, error: Exception) -> None:
        if isinstance(error, BokError):
            self._json(error.status, error.as_dict())
            return
        self._json(500, {"error": {"code": "internal_error", "message": "Bok encountered an internal error"}})

    def _authorized(self) -> bool:
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return False
        except ValueError:
            return False
        authorization = self.headers.get("Authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if supplied and hmac.compare_digest(supplied, self.server.token):
            self.principal = {"kind": "admin", "agent_id": "local-admin", "scopes": ["*"]}
            return True
        principal = self.server.service.authenticate_agent(supplied)
        if principal:
            self.principal = principal
            return True
        return False

    def _require_auth(self) -> None:
        if not self._authorized():
            raise BokError("unauthorized", "A valid local Bok bearer token is required", status=401)
        if not self.server.allow_request(self.client_address[0]):
            raise BokError("rate_limited", "Too many Bok API requests", status=429)

    def _require_scope(self, scope: str) -> None:
        principal = getattr(self, "principal", {})
        if principal.get("kind") == "admin":
            return
        if scope not in set(principal.get("scopes") or []):
            raise BokError("agent_scope_forbidden", "Agent credential does not grant this operation", status=403, details={"required_scope": scope})

    def _require_admin(self) -> None:
        if getattr(self, "principal", {}).get("kind") != "admin":
            raise BokError("admin_required", "This operation requires the trusted local admin token", status=403)

    def _agent_value(self, requested: str) -> str:
        principal = getattr(self, "principal", {})
        return str(principal.get("agent_id", "")) if principal.get("kind") == "agent" else str(requested or "")

    def _body(self) -> dict:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise BokError("invalid_content_length", "Invalid Content-Length header") from error
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise BokError("request_too_large", "Request exceeds the 1 MiB limit", status=413)
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if length and content_type != "application/json":
            raise BokError("unsupported_media_type", "Bok API accepts application/json", status=415)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw)
        except ValueError as error:
            raise BokError("invalid_json", "Request body is not valid JSON") from error
        if not isinstance(value, dict):
            raise BokError("invalid_json", "Request body must be a JSON object")
        return value

    def _query(self) -> dict:
        parsed = urlparse(self.path)
        return {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}

    @staticmethod
    def _int(value, default: int, minimum: int, maximum: int) -> int:
        try:
            return max(minimum, min(maximum, int(value)))
        except (TypeError, ValueError):
            return default

    def do_GET(self) -> None:
        try:
            self._require_auth()
            route = urlparse(self.path).path.rstrip("/")
            query = self._query()
            if getattr(self, "principal", {}).get("kind") == "agent" and route not in {"/v1/health", "/v1/person/health"}:
                self._require_admin()
            if route == "/v1/health":
                payload = self.server.service.health()
            elif route == "/v1/background":
                payload = self.server.service.background.status()
            elif route == "/v1/today":
                payload = self.server.service.today()
            elif route == "/v1/memory/inbox":
                payload = self.server.service.inbox(status=query.get("status", "pending"), limit=self._int(query.get("limit"), 100, 1, 500))
            elif route == "/v1/memory/captures":
                payload = self.server.service.capture_status(query.get("id", ""), limit=self._int(query.get("limit"), 100, 1, 500))
            elif route == "/v1/conversations/status":
                payload = self.server.service.conversation_status(
                    event_id=query.get("event_id", ""),
                    conversation_id=query.get("conversation_id", ""),
                    turn_id=query.get("turn_id", ""),
                    limit=self._int(query.get("limit"), 100, 1, 500),
                )
            elif route == "/v1/person/health":
                payload = self.server.service.person_health()
            elif route == "/v1/person/dashboard":
                self._require_admin()
                payload = self.server.service.person_dashboard(limit=self._int(query.get("limit"), 100, 1, 500))
            elif route == "/v1/person/observations":
                self._require_admin()
                payload = self.server.service.person_observations(
                    status=query.get("status", "all"),
                    limit=self._int(query.get("limit"), 100, 1, 500),
                )
            elif route == "/v1/person/cleanup":
                self._require_admin()
                payload = self.server.service.person_cleanup_candidates(include_dismissed=query.get("include_dismissed") == "true")
            elif route == "/v1/person/claims":
                self._require_admin()
                if query.get("id"):
                    payload = self.server.service.person_claim(query["id"])
                else:
                    payload = self.server.service.person_claims(
                        status=query.get("status", "all"),
                        claim_type=query.get("claim_type", ""),
                        limit=self._int(query.get("limit"), 100, 1, 500),
                    )
            elif route == "/v1/person/claims/explain":
                self._require_admin()
                payload = self.server.service.explain_person_claim(query.get("id", ""))
            elif route == "/v1/person/versions":
                self._require_admin()
                payload = self.server.service.person_claim_versions(
                    query.get("claim_id", ""),
                    limit=self._int(query.get("limit"), 100, 1, 500),
                )
            elif route == "/v1/person/backups":
                self._require_admin()
                payload = self.server.service.person_backup_list(
                    limit=self._int(query.get("limit"), 100, 1, 500),
                )
            elif route == "/v1/backups":
                payload = self.server.service.backup_list(
                    limit=self._int(query.get("limit"), 100, 1, 500),
                )
            elif route == "/v1/agents":
                self._require_admin()
                payload = self.server.service.list_agent_credentials()
            elif route == "/v1/quick-notes":
                payload = self.server.service.list_quick_notes(limit=self._int(query.get("limit"), 100, 1, 500))
            elif route == "/v1/activity":
                payload = self.server.service.activity(self._int(query.get("limit"), 100, 1, 500))
            elif route == "/v1/versions":
                payload = {"items": self.server.service.storage.list_versions(query.get("path") or None, self._int(query.get("limit"), 100, 1, 500))}
            elif route == "/v1/documents/read":
                payload = self.server.service.read_document(query.get("path", ""))
            else:
                raise BokError("route_not_found", "Bok API route does not exist", status=404)
            self._json(200, payload)
        except Exception as error:
            self._error(error)

    def do_POST(self) -> None:
        try:
            self._require_auth()
            route = urlparse(self.path).path.rstrip("/")
            body = self._body()
            agent_routes = {
                "/v1/search",
                "/v1/context",
                "/v1/sources",
                "/v1/project/resume",
                "/v1/memory/capture",
                "/v1/conversations/observe",
                "/v1/person/context",
                "/v1/person/impacts",
                "/v1/person/outcomes",
            }
            if getattr(self, "principal", {}).get("kind") == "agent" and route not in agent_routes:
                self._require_admin()
            key = self.headers.get("Idempotency-Key", "")
            fingerprint = sha256_text(route + "\n" + canonical_json(body))

            def invoke() -> dict:
                service = self.server.service
                if route == "/v1/background":
                    self._require_admin()
                    return service.background.update(body)
                if route == "/v1/search":
                    self._require_scope("vault:read")
                    return service.search(
                        body.get("query", ""),
                        limit=body.get("limit"),
                        token_budget=body.get("token_budget"),
                        path_prefix=str(body.get("path_prefix", "")),
                        tags=body.get("tags") if isinstance(body.get("tags"), list) else None,
                        semantic=body.get("semantic") is not False,
                        explicit_cloud_consent=body.get("cloud_consent") is True,
                        scope=str(body.get("scope", "default")),
                    )
                if route == "/v1/context":
                    self._require_scope("vault:read")
                    return service.context(body.get("task", ""), limit=body.get("limit"), token_budget=body.get("token_budget"), path_prefix=str(body.get("path_prefix", "")), semantic=body.get("semantic") is not False, explicit_cloud_consent=body.get("cloud_consent") is True, scope=str(body.get("scope", "default")))
                if route == "/v1/sources":
                    self._require_scope("vault:read")
                    return service.sources(body.get("query", ""), limit=body.get("limit"), token_budget=body.get("token_budget"), semantic=body.get("semantic") is not False, explicit_cloud_consent=body.get("cloud_consent") is True, scope=str(body.get("scope", "default")))
                if route == "/v1/memory/capture":
                    self._require_scope("memory:capture")
                    return service.capture_memory(body.get("material", ""), source=body.get("source"), explicit_cloud_consent=body.get("cloud_consent") is True)
                if route == "/v1/conversations/observe":
                    self._require_scope("conversation:observe")
                    return service.observe_conversation(
                        conversation_id=str(body.get("conversation_id", "")),
                        turn_id=str(body.get("turn_id", "")),
                        role=str(body.get("role", "user")),
                        content=str(body.get("content", "")),
                        memory_mode=str(body.get("memory_mode", "default")),
                        external_content=body.get("external_content") is True,
                        client=str(body.get("client", "")),
                        agent=self._agent_value(str(body.get("agent", ""))),
                        project=str(body.get("project", "")),
                        personal_signals=body.get("personal_signals") if isinstance(body.get("personal_signals"), list) else None,
                        explicit_cloud_consent=body.get("cloud_consent") is True,
                    )
                if route == "/v1/conversations/reconcile":
                    return service.reconcile_conversations(limit=self._int(body.get("limit"), 100, 1, 500))
                if route == "/v1/person/setup":
                    self._require_admin()
                    return service.setup_personal_core(
                        str(body.get("path", "")),
                        confirm=body.get("confirm") is True,
                    )
                if route == "/v1/person/claims/propose":
                    self._require_admin()
                    return service.propose_person_claim(
                        statement=body.get("statement", ""),
                        claim_type=body.get("claim_type", ""),
                        scope_kind=body.get("scope_kind", "global"),
                        scope_value=body.get("scope_value", ""),
                        confidence=body.get("confidence", 1.0),
                        sensitivity=body.get("sensitivity", "private"),
                        source_refs=body.get("source_refs") if isinstance(body.get("source_refs"), list) else None,
                        expires_at=body.get("expires_at", ""),
                    )
                if route == "/v1/person/claims/confirm":
                    self._require_admin()
                    return service.confirm_person_claim(
                        str(body.get("claim_id", "")),
                        source_ref=str(body.get("source_ref", "")),
                    )
                if route == "/v1/person/claims/authorize":
                    self._require_admin()
                    return service.authorize_person_claim(
                        str(body.get("claim_id", "")),
                        access_scope=body.get("access_scope") if isinstance(body.get("access_scope"), list) else None,
                        source_ref=str(body.get("source_ref", "")),
                    )
                if route == "/v1/person/claims/correct":
                    self._require_admin()
                    return service.correct_person_claim(
                        str(body.get("claim_id", "")),
                        statement=body.get("statement", ""),
                        source_ref=str(body.get("source_ref", "")),
                        scope_kind=str(body.get("scope_kind", "")),
                        scope_value=str(body.get("scope_value", "")),
                    )
                if route == "/v1/person/claims/reject":
                    self._require_admin()
                    return service.reject_person_claim(
                        str(body.get("claim_id", "")),
                        reason=str(body.get("reason", "")),
                        source_ref=str(body.get("source_ref", "")),
                    )
                if route == "/v1/person/claims/forget":
                    self._require_admin()
                    return service.forget_person_claim(
                        str(body.get("claim_id", "")),
                        confirm_forget=body.get("confirm_forget") is True,
                    )
                if route == "/v1/person/claims/supersede":
                    self._require_admin()
                    return service.supersede_person_claim(
                        str(body.get("claim_id", "")),
                        statement=body.get("statement", ""),
                        source_ref=str(body.get("source_ref", "")),
                        scope_kind=str(body.get("scope_kind", "")),
                        scope_value=str(body.get("scope_value", "")),
                    )
                if route == "/v1/person/claims/rollback":
                    self._require_admin()
                    return service.rollback_person_claim(
                        str(body.get("version_id", "")),
                        confirm_important=body.get("confirm_important") is True,
                    )
                if route == "/v1/person/context":
                    self._require_scope("context:read")
                    return service.person_context(
                        task=str(body.get("task", "")),
                        agent=self._agent_value(str(body.get("agent", ""))),
                        project=str(body.get("project", "")),
                        limit=self._int(body.get("limit"), 6, 1, 12),
                        token_budget=self._int(body.get("token_budget"), 1500, 256, service.config.max_context_tokens),
                    )
                if route == "/v1/person/observations/process":
                    self._require_admin()
                    return service.process_person_learning(limit=self._int(body.get("limit"), 100, 1, 500))
                if route == "/v1/person/impacts":
                    self._require_scope("impact:write")
                    return service.record_person_impact(
                        answer_ref=str(body.get("answer_ref", "")),
                        task=str(body.get("task", "")),
                        agent=self._agent_value(str(body.get("agent", ""))),
                        project=str(body.get("project", "")),
                        claim_ids=body.get("claim_ids") if isinstance(body.get("claim_ids"), list) else None,
                    )
                if route == "/v1/person/outcomes":
                    self._require_scope("outcome:write")
                    return service.record_person_outcome(
                        answer_ref=str(body.get("answer_ref", "")),
                        outcome=str(body.get("outcome", "")),
                        claim_ids=body.get("claim_ids") if isinstance(body.get("claim_ids"), list) else None,
                        source_ref=str(body.get("source_ref", "")),
                        agent=self._agent_value(str(body.get("agent", ""))),
                        project=str(body.get("project", "")),
                        rating=body.get("rating", 0),
                        rework=body.get("rework") is True,
                        note=str(body.get("note", "")),
                    )
                if route == "/v1/person/cleanup":
                    self._require_admin()
                    return service.person_cleanup_action(
                        str(body.get("claim_id", "")),
                        action=str(body.get("action", "")),
                        confirm_important=body.get("confirm_important") is True,
                    )
                if route == "/v1/person/backups/create":
                    self._require_admin()
                    return service.person_backup_create()
                if route == "/v1/person/backups/verify":
                    self._require_admin()
                    return service.person_backup_verify(str(body.get("backup_id", "")))
                if route == "/v1/person/backups/restore":
                    self._require_admin()
                    return service.person_backup_restore(
                        str(body.get("backup_id", "")),
                        confirm_personal_core=str(body.get("confirm_personal_core", "")),
                        mode=str(body.get("mode", "exact")),
                    )
                if route == "/v1/agents/issue":
                    self._require_admin()
                    return service.issue_agent_credential(
                        str(body.get("agent_id", "")),
                        scopes=body.get("scopes") if isinstance(body.get("scopes"), list) else None,
                    )
                if route == "/v1/agents/revoke":
                    self._require_admin()
                    return service.revoke_agent_credential(str(body.get("agent_id", "")))
                if route == "/v1/memory/process":
                    return service.process_captures(limit=self._int(body.get("limit"), 3, 1, 20))
                if route == "/v1/memory/propose":
                    return service.propose_memory(body.get("material", ""), source=body.get("source"), explicit_cloud_consent=body.get("cloud_consent") is True)
                if route == "/v1/memory/commit":
                    return service.commit_memory(str(body.get("proposal_id", "")), confirm_important=body.get("confirm_important") is True)
                if route == "/v1/memory/reject":
                    return service.reject_memory(str(body.get("proposal_id", "")), reason=str(body.get("reason", "")))
                if route == "/v1/memory/rollback":
                    return service.rollback_memory(str(body.get("proposal_id", "")), confirm_important=body.get("confirm_important") is True)
                if route == "/v1/project/resume":
                    self._require_scope("vault:read")
                    return service.project_resume(str(body.get("path", "")), token_budget=body.get("token_budget"))
                if route == "/v1/quick-notes":
                    return service.create_quick_note(body.get("text", ""), source=str(body.get("source", "desktop")))
                if route == "/v1/web-clips":
                    return service.create_web_clip(title=body.get("title", ""), url=body.get("url", ""), content=body.get("content", ""), tags=body.get("tags") if isinstance(body.get("tags"), list) else None)
                if route == "/v1/import/markdown":
                    return service.import_markdown(text=body.get("text", ""), title=body.get("title", ""), destination=body.get("destination", ""))
                if route == "/v1/quick-notes/promote":
                    return service.promote_quick_note(str(body.get("path", "")))
                if route == "/v1/quick-notes/archive":
                    return service.archive_quick_note(str(body.get("path", "")), expected_hash=str(body.get("expected_hash", "")))
                if route == "/v1/documents/write":
                    return service.write_document(str(body.get("path", "")), str(body.get("text", "")), expected_hash=body.get("expected_hash"), important=body.get("important") is True, confirm_important=body.get("confirm_important") is True)
                if route == "/v1/documents/trash":
                    return service.trash_document(str(body.get("path", "")), expected_hash=body.get("expected_hash"), confirm_important=body.get("confirm_important") is True)
                if route == "/v1/documents/move":
                    return service.move_document(str(body.get("source", "")), str(body.get("destination", "")), expected_hash=str(body.get("expected_hash", "")), confirm_important=body.get("confirm_important") is True)
                if route == "/v1/documents/rollback":
                    return service.rollback_document(str(body.get("version_id", "")), confirm_important=body.get("confirm_important") is True)
                if route == "/v1/backups/create":
                    return service.backup_create()
                if route == "/v1/backups/verify":
                    return service.backup_verify(str(body.get("backup_id", "")))
                if route == "/v1/backups/restore":
                    return service.backup_restore(
                        str(body.get("backup_id", "")),
                        confirm_vault=str(body.get("confirm_vault", "")),
                        mode=str(body.get("mode", "exact")),
                    )
                if route == "/v1/auth/rotate":
                    self._require_admin()
                    token = service.rotate_auth_token()
                    self.server.token = token
                    return {"rotated": True, "token": token}
                raise BokError("route_not_found", "Bok API route does not exist", status=404)

            mutation = route not in {"/v1/search", "/v1/context", "/v1/sources", "/v1/project/resume", "/v1/person/context"}
            if route in {"/v1/auth/rotate", "/v1/agents/issue"}:
                payload = invoke()
            else:
                payload = self.server.idempotency.run(key, fingerprint, invoke) if mutation else invoke()
            status = 202 if route in {"/v1/memory/capture", "/v1/conversations/observe"} else 200
            self._json(status, payload)
        except Exception as error:
            self._error(error)

    def do_OPTIONS(self) -> None:
        self._json(405, {"error": {"code": "cors_disabled", "message": "Cross-origin browser access is disabled"}})


def create_server(config: BokConfig) -> BokAPIServer:
    service = BokService(config)
    service.initialize()
    server = BokAPIServer((config.host, config.port), service)
    server.start_capture_worker()
    return server


def serve(config: BokConfig) -> None:
    server = create_server(config)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
