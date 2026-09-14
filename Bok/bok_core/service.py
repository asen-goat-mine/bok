from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .auth import AgentCredentialStore
from .background import BackgroundProcessing
from .config import BokConfig
from .conversation import ConversationLedger
from .errors import BokError, NotFoundError
from .markdown import parse_frontmatter, render_frontmatter
from .memory import MemoryInbox
from .person import PersonalClaimStore
from .person_learning import PersonalLearningStore
from .provider import CredentialStore, ProviderClient
from .search import VaultSearch
from .storage import VaultStorage
from .util import atomic_write_json, atomic_write_text, canonical_json, read_json, sha256_text, slugify, utc_now
from .version import VERSION


class BokService:
    API_VERSION = "v1"
    SERVICE_VERSION = VERSION

    def __init__(self, config: BokConfig):
        self.config = config
        self.storage = VaultStorage(config)
        self.background = BackgroundProcessing(self.storage)
        self.search_engine = VaultSearch(config, self.storage)
        self.memory = MemoryInbox(config, self.storage, self.search_engine)
        self.conversations = ConversationLedger(config, self.memory)
        self.person = PersonalClaimStore(config)
        self.person_learning = PersonalLearningStore(config, self.person)
        self._person_setup_lock = self.storage.lock
        self.agent_credentials = AgentCredentialStore(config.state_dir)
        self.provider = ProviderClient(config)
        self.credentials = CredentialStore(config)

    def initialize(self) -> dict:
        self.storage.ensure_state()
        restore_repair = self.storage.repair_restore_transactions()
        version_repair = self.storage.repair_versions()
        self._ensure_token()
        capture_markers = self.memory.repair_capture_markers()
        personal_profile_reconcile = self.memory.reconcile_personal_profile_proposals()
        self.conversations.repair_summary()
        retention = self.conversations.purge_expired_content()
        reconciled = self.conversations.reconcile(limit=500)
        conversation_summary = self.conversations.repair_summary()
        personal_core = self.person.initialize()
        personal_learning = self.person_learning.initialize()
        index = self.search_engine.refresh(force=True)
        return {
            "initialized": True,
            "index": index,
            "state_dir": str(self.config.state_dir),
            "restore_repair": restore_repair,
            "version_repair": version_repair,
            "capture_markers": capture_markers,
            "personal_profile_reconcile": personal_profile_reconcile,
            "conversation_summary": conversation_summary,
            "conversation_reconcile": reconciled,
            "conversation_retention": retention,
            "personal_core": personal_core,
            "personal_learning": personal_learning,
        }

    def _ensure_token(self) -> Path:
        self.storage.ensure_state()
        token_path = self.config.state_dir / "auth-token"
        if not token_path.is_file():
            atomic_write_text(token_path, secrets.token_urlsafe(32) + "\n")
        return token_path

    def auth_token(self) -> str:
        return self._ensure_token().read_text(encoding="utf-8").strip()

    def rotate_auth_token(self) -> str:
        token = secrets.token_urlsafe(32)
        atomic_write_text(self._ensure_token(), token + "\n")
        return token

    def health(self) -> dict:
        index = self.search_engine.refresh()
        return {
            "ready": True,
            "service": "bok-memory",
            "version": self.SERVICE_VERSION,
            "api_version": self.API_VERSION,
            "vault": self.config.vault_root.name,
            "local_only": self.config.local_only,
            "background": self.background.status(),
            "provider": self.provider.info(),
            "index": index,
            "memory_inbox": self.memory.counts(),
            "conversation_ledger": self.conversations.counts(),
            "personal_core": self.person.health(),
            "personal_learning": self.person_learning.health(),
            "agent_credentials": {"count": len(self.agent_credentials.list()["items"])},
            "capabilities": [
                "search",
                "context",
                "sources",
                "memory.propose",
                "memory.commit",
                "memory.rollback",
                "conversations.observe",
                "conversations.status",
                "conversations.reconcile",
                "person.setup",
                "person.claim.propose",
                "person.claim.confirm",
                "person.claim.authorize",
                "person.claim.correct",
                "person.claim.reject",
                "person.claim.forget",
                "person.claim.supersede",
                "person.claim.rollback",
                "person.context",
                "person.observation.process",
                "person.impact.record",
                "person.outcome.record",
                "person.dashboard",
                "person.cleanup",
                "person.backup.create",
                "person.backup.restore",
                "agent.issue",
                "agent.revoke",
                "project.resume",
                "quick-note.create",
                "document.write",
                "document.rollback",
                "backup.create",
                "backup.restore",
            ],
        }

    def setup_personal_core(self, path: str, *, confirm: bool = False) -> dict:
        if not confirm:
            raise BokError(
                "personal_core_confirmation_required",
                "Creating or changing the Personal Core requires explicit confirmation",
                status=403,
            )
        with self._person_setup_lock:
            resolved = self.config.validate_personal_core_path(path)
            marker = resolved / "PERSONAL-CORE.md"
            if marker.is_symlink():
                raise BokError("unsafe_personal_core", "Personal Core marker cannot be a symbolic link", status=403)
            if resolved.exists() and any(resolved.iterdir()) and not marker.is_file():
                raise BokError(
                    "personal_core_not_empty",
                    "Choose an empty folder or an existing Bok Personal Core",
                    status=409,
                )
            candidate = PersonalClaimStore(replace(self.config, personal_core_root=str(resolved)))
            health = candidate.initialize()
            learning = PersonalLearningStore(candidate.config, candidate)
            learning_health = learning.initialize()
            data = read_json(self.config.config_path, {})
            if not isinstance(data, dict):
                data = {}
            data["personal_core_root"] = str(resolved)
            atomic_write_json(self.config.config_path, data)
            self.config.personal_core_root = str(resolved)
            candidate.config = self.config
            learning.config = self.config
            self.person = candidate
            self.person_learning = learning
        return {
            "configured": health["configured"],
            "ready": health["ready"],
            "name": health.get("name", ""),
            "config_saved": True,
            "learning_ready": learning_health["ready"],
        }

    def person_health(self) -> dict:
        health = self.person.health()
        health["learning"] = self.person_learning.health()
        return health

    def propose_person_claim(self, **values) -> dict:
        return self.person.propose_explicit(**values)

    def person_claim(self, claim_id: str) -> dict:
        return self.person.get(claim_id)

    def person_claims(self, **options) -> dict:
        return self.person.list(**options)

    def confirm_person_claim(self, claim_id: str, **options) -> dict:
        return self.person.confirm(claim_id, **options)

    def authorize_person_claim(self, claim_id: str, **options) -> dict:
        return self.person.authorize(claim_id, **options)

    def correct_person_claim(self, claim_id: str, **values) -> dict:
        return self.person.correct(claim_id, **values)

    def reject_person_claim(self, claim_id: str, **values) -> dict:
        return self.person.reject(claim_id, **values)

    def supersede_person_claim(self, claim_id: str, **values) -> dict:
        return self.person.supersede(claim_id, **values)

    def explain_person_claim(self, claim_id: str) -> dict:
        return self.person.explain(claim_id)

    def person_claim_versions(self, claim_id: str, *, limit: int = 100) -> dict:
        return self.person.versions(claim_id, limit=limit)

    def rollback_person_claim(self, version_id: str, *, confirm_important: bool = False) -> dict:
        return self.person.rollback(version_id, confirm_important=confirm_important)

    def person_context(self, *, task: str, agent: str, project: str = "", limit: int = 6, token_budget: int = 1500) -> dict:
        return self.person.context(task=task, agent=agent, project=project, limit=limit, token_budget=token_budget)

    def person_observations(self, **options) -> dict:
        return self.person_learning.observations(**options)

    def process_person_learning(self, *, limit: int = 100) -> dict:
        return self.person_learning.process(limit=limit)

    def person_dashboard(self, *, limit: int = 100) -> dict:
        if not self.person.configured:
            return {"configured": False, "ready": False, "reason": "personal_core_not_configured"}
        dashboard = self.person_learning.dashboard(limit=limit)
        credentials = self.agent_credentials.list()["items"]
        dashboard["permissions"] = {
            "default": "personal-core",
            "agents": credentials,
            "active_count": sum(1 for item in credentials if item.get("status") == "active"),
        }
        return dashboard

    def record_person_impact(self, **values) -> dict:
        return self.person_learning.record_impact(**values)

    def record_person_outcome(self, **values) -> dict:
        return self.person_learning.record_outcome(**values)

    def person_cleanup_candidates(self, *, include_dismissed: bool = False) -> dict:
        return self.person_learning.cleanup_candidates(include_dismissed=include_dismissed)

    def person_cleanup_action(self, claim_id: str, **values) -> dict:
        return self.person_learning.cleanup_action(claim_id, **values)

    @staticmethod
    def _contains_runtime_reference(value, references: set[str]) -> bool:
        if isinstance(value, dict):
            return any(BokService._contains_runtime_reference(item, references) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(BokService._contains_runtime_reference(item, references) for item in value)
        return isinstance(value, str) and value in references

    def _forget_idempotency_references(self, references) -> int:
        """Drop cached API responses that point at content being forgotten."""
        cleaned = {str(item) for item in references if str(item)}
        if not cleaned:
            return 0
        path = self.config.state_dir / "state" / "idempotency.json"
        with self.storage.lock:
            state = read_json(path, {})
            if not isinstance(state, dict):
                return 0
            kept = {}
            removed = 0
            for key, value in state.items():
                key_matches = any(reference in str(key) for reference in cleaned)
                if key_matches or self._contains_runtime_reference(value, cleaned):
                    removed += 1
                else:
                    kept[key] = value
            if removed:
                atomic_write_json(path, kept)
            return removed

    def forget_person_claim(self, claim_id: str, *, confirm_forget: bool = False) -> dict:
        result = self.person_learning.forget_claim(claim_id, confirm_forget=confirm_forget)
        forgotten_turns = []
        runtime_references = {claim_id}
        derived_memory = []
        for source in result.pop("source_turns", []):
            forgotten = self.conversations.forget_turn(**source)
            forgotten_turns.append(forgotten)
            runtime_references.add(str(forgotten.get("event_id", "")))
            capture = forgotten.get("capture") if isinstance(forgotten.get("capture"), dict) else {}
            runtime_references.add(str(capture.get("capture_id", "")))
            runtime_references.add(str(capture.get("proposal_id", "")))
            derived_memory.extend(str(item) for item in capture.get("derived_memory_requiring_review", []) if str(item))
        result["forgotten_source_turns"] = forgotten_turns
        result["idempotency_receipts_removed"] = self._forget_idempotency_references(runtime_references)
        result["derived_memory_requiring_review"] = list(dict.fromkeys(derived_memory))
        return result

    def person_backup_list(self, *, limit: int = 100) -> dict:
        return self.person.list_backups(limit=limit)

    def person_backup_create(self) -> dict:
        return self.person.create_backup()

    def person_backup_verify(self, backup_id: str) -> dict:
        return self.person.verify_backup(backup_id)

    def person_backup_restore(self, backup_id: str, *, confirm_personal_core: str, mode: str = "exact") -> dict:
        result = self.person.restore_backup(backup_id, confirm_personal_core=confirm_personal_core, mode=mode)
        self.person_learning._cache.clear()
        return result

    def issue_agent_credential(self, agent_id: str, *, scopes=None) -> dict:
        return self.agent_credentials.issue(agent_id, scopes=scopes)

    def revoke_agent_credential(self, agent_id: str) -> dict:
        return self.agent_credentials.revoke(agent_id)

    def list_agent_credentials(self) -> dict:
        return self.agent_credentials.list()

    def authenticate_agent(self, token: str):
        return self.agent_credentials.verify(token)

    def search(self, query: str, **options) -> dict:
        return self.search_engine.search(query, **options)

    def context(self, task: str, **options) -> dict:
        return self.search_engine.context(task, **options)

    def sources(self, query: str, **options) -> dict:
        context = self.context(query, **options)
        return {"query": query, "sources": context["sources"], "token_estimate": context["token_estimate"]}

    def propose_memory(self, material: str, *, source=None, explicit_cloud_consent: bool = False) -> dict:
        return self.memory.propose(material, source=source, explicit_cloud_consent=explicit_cloud_consent)

    def capture_memory(self, material: str, *, source=None, explicit_cloud_consent: bool = False) -> dict:
        return self.memory.capture(material, source=source, explicit_cloud_consent=explicit_cloud_consent)

    def process_captures(self, *, limit: int = 3, force: bool = True) -> dict:
        return self.memory.process_captures(limit=limit, force=force)

    def process_background(self) -> dict:
        settings = self.background.status()
        if settings["paused"]:
            return settings
        self.process_captures(limit=settings["batch_limit"], force=False)
        # Pause may arrive while a model request is in flight. Let that request
        # finish, then stop before scheduling work for the other queue.
        if not self.background.status()["paused"]:
            self.process_person_learning(limit=settings["batch_limit"])
        return self.background.status()

    def capture_status(self, capture_id: str = "", *, limit: int = 100) -> dict:
        return self.memory.capture_status(capture_id, limit=limit)

    def observe_conversation(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        role: str,
        content: str,
        memory_mode: str = "default",
        external_content: bool = False,
        client: str = "",
        agent: str = "",
        project: str = "",
        personal_signals=None,
        explicit_cloud_consent: bool = False,
    ) -> dict:
        signal_hash = sha256_text(canonical_json(personal_signals)) if isinstance(personal_signals, list) else ""
        privacy_filtered = memory_mode == "default" and self.person_learning._looks_sensitive(content)
        ledger_memory_mode = "do_not_remember" if privacy_filtered else memory_mode
        receipt = self.conversations.observe(
            conversation_id=conversation_id,
            turn_id=turn_id,
            role=role,
            content=content,
            memory_mode=ledger_memory_mode,
            external_content=external_content,
            client=client,
            agent=agent,
            project=project,
            person_signal_hash=signal_hash,
            explicit_cloud_consent=explicit_cloud_consent,
        )
        if role == "user" and memory_mode == "default" and not external_content:
            try:
                receipt["personal_learning"] = self.person_learning.observe_turn(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    content=content,
                    agent=agent,
                    project=project,
                    occurred_at=str(receipt.get("created_at", "")),
                    signals=personal_signals,
                )
            except BokError as error:
                receipt["personal_learning"] = {"status": "needs_attention", "reason": error.code, "observations": []}
        else:
            receipt["personal_learning"] = {"status": "excluded", "observations": []}
        if privacy_filtered:
            receipt["privacy_filtered"] = True
        return receipt

    def conversation_status(
        self,
        *,
        event_id: str = "",
        conversation_id: str = "",
        turn_id: str = "",
        limit: int = 100,
    ) -> dict:
        return self.conversations.status(
            event_id=event_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            limit=limit,
        )

    def reconcile_conversations(self, *, limit: int = 100) -> dict:
        return self.conversations.reconcile(limit=limit)

    def purge_expired_conversation_content(self) -> dict:
        return self.conversations.purge_expired_content()

    def commit_memory(self, proposal_id: str, *, confirm_important: bool = False) -> dict:
        return self.memory.commit(proposal_id, confirm_important=confirm_important)

    def rollback_memory(self, proposal_id: str, *, confirm_important: bool = False) -> dict:
        return self.memory.rollback(proposal_id, confirm_important=confirm_important)

    def reject_memory(self, proposal_id: str, *, reason: str = "") -> dict:
        return self.memory.reject(proposal_id, reason=reason)

    def inbox(self, *, status: str = "pending", limit: int = 100) -> dict:
        return {"items": self.memory.list(status=status, limit=limit), "counts": self.memory.counts()}

    def create_quick_note(self, text: str, *, source: str = "desktop") -> dict:
        value = str(text or "").strip()
        if not value:
            raise BokError("empty_note", "Quick note cannot be empty")
        if len(value) > 20000:
            raise BokError("note_too_large", "Quick note exceeds the 20,000 character limit", status=413)
        now = utc_now()
        note_id = uuid.uuid4().hex
        filename_time = re.sub(r"[-:TZ.]", "", now)[:14]
        relative = f"07-Quick-Notes/{filename_time}-{note_id[:6]}.md"
        safe_source = re.sub(r"\s+", " ", str(source or "desktop")).strip()[:80] or "desktop"
        frontmatter = render_frontmatter({
            "id": note_id,
            "type": "quick-note",
            "status": "inbox",
            "created": now,
            "updated": now,
            "source": safe_source,
            "promoted_to": None,
        })
        result = self.storage.write(relative, frontmatter + value + "\n", operation="quick_note_create", metadata={"source": safe_source})
        self.search_engine.invalidate()
        return result.as_dict()

    def create_web_clip(self, *, title: str, url: str, content: str, tags=None) -> dict:
        title = str(title or "").strip()[:240] or "Web Clip"
        url = str(url or "").strip()[:2000]
        content = str(content or "").strip()
        if not content and not url:
            raise BokError("empty_clip", "Web clip requires content or a source URL")
        if len(content) > 500000:
            raise BokError("clip_too_large", "Web clip content exceeds 500,000 characters", status=413)
        now = utc_now()
        clip_id = uuid.uuid4().hex
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        relative = f"01-Inbox/Web-Clips/{day}-{slugify(title, 'web-clip')}-{clip_id[:6]}.md"
        values = [str(item).strip() for item in (tags or []) if str(item).strip()][:20]
        frontmatter = render_frontmatter({
            "id": clip_id,
            "type": "web-clip",
            "status": "inbox",
            "title": title,
            "source_url": url,
            "created": now,
            "updated": now,
            "tags": values,
        })
        body = f"# {title}\n\n"
        if url:
            body += f"来源：{url}\n\n"
        body += content + "\n"
        result = self.storage.write(relative, frontmatter + body, operation="web_clip_create", metadata={"source_host": re.sub(r"^https?://([^/]+).*$", r"\1", url)[:200] if url else ""})
        self.search_engine.invalidate()
        return result.as_dict()

    def import_markdown(self, *, text: str, title: str = "", destination: str = "") -> dict:
        value = str(text or "")
        if not value.strip():
            raise BokError("empty_import", "Imported Markdown cannot be empty")
        if len(value) > 800000:
            raise BokError("import_too_large", "Imported Markdown exceeds 800,000 characters", status=413)
        if destination:
            relative = str(destination).replace("\\", "/")
        else:
            name = slugify(title or "imported-note", "imported-note")
            relative = f"01-Inbox/Imports/{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-{name}-{uuid.uuid4().hex[:6]}.md"
        if self.storage.content_hash(relative) is not None:
            raise BokError("import_destination_exists", "Import destination already exists", status=409, details={"path": relative})
        if not value.endswith("\n"):
            value += "\n"
        result = self.storage.write(relative, value, operation="markdown_import", metadata={"title": str(title)[:240]})
        self.search_engine.invalidate()
        return result.as_dict()

    def list_quick_notes(self, *, limit: int = 100) -> dict:
        directory = self.config.vault_root / "07-Quick-Notes"
        items = []
        if directory.is_dir() and not directory.is_symlink():
            for path in sorted(directory.glob("*.md"), reverse=True):
                if path.is_symlink():
                    continue
                relative = self.storage.relative(path)
                try:
                    text = self.storage.read_text(relative)
                except BokError:
                    continue
                frontmatter, body = parse_frontmatter(text)
                items.append({
                    "path": relative,
                    "id": frontmatter.get("id", ""),
                    "status": frontmatter.get("status", "inbox"),
                    "created": frontmatter.get("created", ""),
                    "preview": body.strip()[:240],
                    "content_hash": self.storage.content_hash(relative),
                })
                if len(items) >= max(1, min(limit, 500)):
                    break
        return {"items": items}

    def promote_quick_note(self, path: str) -> dict:
        text = self.storage.read_text(path)
        frontmatter, body = parse_frontmatter(text)
        if str(frontmatter.get("type", "")) != "quick-note":
            raise BokError("not_quick_note", "The selected document is not a Bok quick note")
        return self.capture_memory(body.strip(), source={"type": "quick-note", "ref": path})

    def archive_quick_note(self, path: str, *, expected_hash: str) -> dict:
        text = self.storage.read_text(path)
        frontmatter, body = parse_frontmatter(text)
        if str(frontmatter.get("type", "")) != "quick-note":
            raise BokError("not_quick_note", "The selected document is not a Bok quick note")
        frontmatter["status"] = "archived"
        frontmatter["updated"] = utc_now()
        result = self.storage.write(path, render_frontmatter(frontmatter) + body.lstrip("\r\n"), expected_hash=expected_hash, operation="quick_note_archive")
        self.search_engine.invalidate()
        return result.as_dict()

    def read_document(self, path: str) -> dict:
        text = self.storage.read_text(path)
        frontmatter, _ = parse_frontmatter(text)
        normalized = path.replace("\\", "/")
        document = self.search_engine.document(normalized)
        metadata = {
            "type": document.document_type if document else str(frontmatter.get("type") or "note"),
            "role": document.document_role if document else str(frontmatter.get("role") or "note"),
            "status": document.status if document else str(frontmatter.get("status") or "active"),
            "source": document.source if document else str(frontmatter.get("source") or frontmatter.get("source_type") or "unspecified"),
            "updated": document.updated if document else str(frontmatter.get("updated") or ""),
            "updated_source": document.updated_source if document else "frontmatter",
            "tags": document.tags if document else [],
            "aliases": document.aliases if document else [],
        }
        return {"path": normalized, "text": text, "content_hash": self.storage.content_hash(path), "frontmatter": frontmatter, "metadata": metadata}

    def write_document(self, path: str, text: str, *, expected_hash: Optional[str], important: bool = False, confirm_important: bool = False) -> dict:
        existing_hash = self.storage.content_hash(path)
        if existing_hash is not None and expected_hash is None:
            raise BokError("precondition_required", "Editing an existing document requires its expected_hash", status=428, details={"path": path})
        existing_important = False
        if existing_hash is not None:
            frontmatter, _ = parse_frontmatter(self.storage.read_text(path))
            existing_important = str(frontmatter.get("importance", "")).casefold() == "important" or str(frontmatter.get("memory_type", "")).casefold() in set(self.config.important_memory_types)
        incoming_frontmatter, _ = parse_frontmatter(str(text))
        incoming_important = str(incoming_frontmatter.get("importance", "")).casefold() == "important" or str(incoming_frontmatter.get("memory_type", "")).casefold() in set(self.config.important_memory_types)
        if (important or existing_important or incoming_important) and not confirm_important:
            raise BokError("important_confirmation_required", "Important memory modification requires explicit confirmation", status=403)
        result = self.storage.write(path, text, expected_hash=expected_hash, operation="document_write", metadata={"important": bool(important or incoming_important)})
        self.search_engine.invalidate()
        return result.as_dict()

    def trash_document(self, path: str, *, expected_hash: Optional[str], confirm_important: bool = False) -> dict:
        actual_hash = self.storage.content_hash(path)
        if actual_hash is None:
            raise NotFoundError("Markdown document does not exist", details={"path": path})
        if expected_hash is None:
            raise BokError("precondition_required", "Trashing a document requires its expected_hash", status=428, details={"path": path})
        frontmatter, _ = parse_frontmatter(self.storage.read_text(path))
        important = str(frontmatter.get("importance", "")).casefold() == "important" or str(frontmatter.get("memory_type", "")).casefold() in set(self.config.important_memory_types)
        if important and not confirm_important:
            raise BokError("important_confirmation_required", "Important memory deletion requires explicit confirmation", status=403)
        result = self.storage.delete(path, expected_hash=expected_hash)
        self.search_engine.invalidate()
        return result

    def move_document(self, source: str, destination: str, *, expected_hash: str, confirm_important: bool = False) -> dict:
        frontmatter, _ = parse_frontmatter(self.storage.read_text(source))
        important = str(frontmatter.get("importance", "")).casefold() == "important" or str(frontmatter.get("memory_type", "")).casefold() in set(self.config.important_memory_types)
        if important and not confirm_important:
            raise BokError("important_confirmation_required", "Moving important memory requires explicit confirmation", status=403)
        result = self.storage.move(source, destination, expected_hash=expected_hash)
        self.search_engine.invalidate()
        return result

    def rollback_document(self, version_id: str, *, confirm_important: bool = False) -> dict:
        record = self.storage.version_record(version_id)
        path = str(record.get("path", ""))
        important = False
        current_hash = self.storage.content_hash(path)
        if current_hash is not None:
            frontmatter, _ = parse_frontmatter(self.storage.read_text(path))
            important = str(frontmatter.get("importance", "")).casefold() == "important" or str(frontmatter.get("memory_type", "")).casefold() in set(self.config.important_memory_types)
        before_path = self.storage.versions / version_id / "before.md"
        if before_path.is_file():
            frontmatter, _ = parse_frontmatter(before_path.read_text(encoding="utf-8-sig", errors="replace"))
            important = important or str(frontmatter.get("importance", "")).casefold() == "important" or str(frontmatter.get("memory_type", "")).casefold() in set(self.config.important_memory_types)
        if important and not confirm_important:
            raise BokError("important_confirmation_required", "Rolling back important memory requires explicit confirmation", status=403)
        result = self.storage.rollback(version_id)
        self.search_engine.invalidate()
        return result.as_dict()

    @staticmethod
    def _section(text: str, titles) -> str:
        pattern = r"(?ims)^#{1,3}\s*(?:" + "|".join(re.escape(title) for title in titles) + r")\s*$\n(.*?)(?=^#{1,3}\s|\Z)"
        match = re.search(pattern, text)
        return match.group(1).strip() if match else ""

    def project_resume(self, path: str = "", *, token_budget: Optional[int] = None) -> dict:
        if not path:
            path = self.search_engine.focus_path()
        if not path:
            raise NotFoundError("No focus_path is configured in Active-Context.md")
        text = self.storage.read_text(path)
        document = self.search_engine.document(path)
        title = document.title if document else Path(path).stem
        status = self._section(text, ["当前状态", "状态", "Current Status"])
        decisions = self._section(text, ["关键决策", "当前决定", "已确认的产品决策", "Decisions"])
        next_actions = self._section(text, ["下一步行动", "后续行动", "下一步", "Next"])
        blocking = self._section(text, ["阻塞项", "阻塞", "Blockers"])
        query = f"{title} 当前状态 关键决策 下一步"
        context = self.context(query, limit=4, token_budget=token_budget or min(1600, self.config.max_context_tokens))
        return {
            "path": path,
            "title": title,
            "status": status,
            "decisions": decisions,
            "next_actions": next_actions,
            "blockers": blocking,
            "context": context["context"],
            "sources": context["sources"],
            "token_estimate": context["token_estimate"],
        }

    def backup_create(self) -> dict:
        return self.storage.create_backup()

    def backup_list(self, *, limit: int = 100) -> dict:
        return self.storage.list_backups(limit=limit)

    def backup_verify(self, backup_id: str) -> dict:
        return self.storage.verify_backup(backup_id)

    def backup_restore(self, backup_id: str, *, confirm_vault: str, mode: str = "exact") -> dict:
        result = self.storage.restore_backup(backup_id, confirm_vault=confirm_vault, mode=mode)
        self.search_engine.invalidate()
        return result

    def activity(self, limit: int = 100) -> dict:
        return {"items": self.storage.recent_activity(limit)}

    def today(self) -> dict:
        try:
            project = self.project_resume(token_budget=min(1200, self.config.max_context_tokens))
        except BokError:
            project = None
        pending = self.memory.list(status="pending", limit=20)
        captures = self.memory.capture_status(limit=50)["items"]
        attention = [item for item in captures if item.get("status") in {"needs_attention", "waiting_for_model"}]
        recent = self.storage.recent_activity(12)
        return {
            "project": project,
            "attention": {
                "important_memories": [item for item in pending if item.get("requires_review")],
                "other_pending": [item for item in pending if not item.get("requires_review")],
                "captures": attention,
                "count": len(pending) + len(attention),
            },
            "recent_activity": recent,
            "quiet_mode": {
                "ordinary_memory": "auto_commit_with_undo",
                "important_memory": "non_blocking_review",
                "cloud": "explicit_consent_only",
            },
        }

    def set_credential(self, name: str, secret: str) -> dict:
        self.credentials.set(name, secret)
        return {"stored": True, "reference": f"keychain:{name}"}
