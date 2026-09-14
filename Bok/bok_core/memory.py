from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .config import BokConfig
from .errors import BokError, ConflictError, NotFoundError, PermissionDeniedError
from .markdown import parse_frontmatter, render_frontmatter
from .provider import MemoryIntelligence
from .search import VaultSearch
from .storage import VaultStorage
from .util import atomic_write_json, read_json, sha256_text, slugify, utc_now


class MemoryInbox:
    """Non-blocking proposal queue with conservative auto-commit rules."""

    BACKGROUND_BATCH_MIN = 10
    BACKGROUND_BATCH_MAX_WAIT_SECONDS = 30
    PERSONAL_PROFILE_MEMORY_TYPES = {"preference", "identity"}

    def __init__(self, config: BokConfig, storage: VaultStorage, search: VaultSearch):
        self.config = config
        self.storage = storage
        self.search = search
        self.intelligence = MemoryIntelligence(config)
        self.path = config.state_dir / "state" / "memory-inbox.json"
        # Migration source for v0.1 installations; valid records are copied to
        # shards and the old raw queue is atomically cleared on startup.
        self.capture_path = config.state_dir / "state" / "capture-queue.json"
        self.capture_dir = config.state_dir / "state" / "captures"
        self.capture_hash_dir = config.state_dir / "state" / "capture-hashes"
        self.capture_pending_dir = config.state_dir / "state" / "capture-pending"
        self.lock = storage.lock

    def _load(self) -> List[dict]:
        value = read_json(self.path, [])
        return value if isinstance(value, list) else []

    def _save(self, proposals: List[dict]) -> None:
        self.storage.ensure_state()
        atomic_write_json(self.path, proposals)

    def list(self, *, status: str = "pending", limit: int = 100) -> List[dict]:
        proposals = self._load()
        if status and status != "all":
            proposals = [item for item in proposals if item.get("status") == status]
        return list(reversed(proposals[-max(1, min(limit, 500)):]))

    def reconcile_personal_profile_proposals(self) -> dict:
        """Move duplicate conversational profile cards out of Vault review."""
        migrated = 0
        with self.lock:
            proposals = self._load()
            for proposal in proposals:
                source = proposal.get("source") if isinstance(proposal.get("source"), dict) else {}
                analysis = proposal.get("analysis") if isinstance(proposal.get("analysis"), dict) else {}
                if (
                    proposal.get("status") in {"pending", "ready"}
                    and str(source.get("type", "")).casefold() == "conversation"
                    and str(analysis.get("memory_type", "")).casefold() in self.PERSONAL_PROFILE_MEMORY_TYPES
                ):
                    proposal["status"] = "personal_core_only"
                    proposal["requires_review"] = False
                    proposal["review_reasons"] = []
                    proposal["reconciled_at"] = utc_now()
                    proposal["reconcile_reason"] = "personal_profile_is_managed_by_personal_core"
                    migrated += 1
            if migrated:
                self._save(proposals)
        return {"migrated": migrated}

    def get(self, proposal_id: str) -> dict:
        for item in self._load():
            if item.get("id") == proposal_id:
                return item
        raise NotFoundError("Memory proposal does not exist", details={"proposal_id": proposal_id})

    @staticmethod
    def _source_value(source) -> dict:
        def clean(value, limit: int) -> str:
            return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]

        if isinstance(source, dict):
            return {
                "type": clean(source.get("type", "conversation"), 40) or "conversation",
                "ref": clean(source.get("ref", ""), 240),
                "at": clean(source.get("at", utc_now()), 40) or utc_now(),
            }
        return {"type": "conversation", "ref": clean(source, 240), "at": utc_now()}

    def _target_path(self, analysis: dict, proposal_id: str) -> str:
        candidate = str(analysis.get("target_path", "")).replace("\\", "/").strip()
        if candidate:
            try:
                self.storage.resolve(candidate, write=True)
                return candidate
            except PermissionDeniedError:
                pass
        memory_type = analysis.get("memory_type")
        root = "02-Projects" if memory_type in {"project_status", "action", "decision"} else "03-Knowledge"
        title = analysis.get("title") or analysis.get("summary") or proposal_id
        return f"{root}/{slugify(str(title), proposal_id)}.md"

    def _review_reasons(self, analysis: dict, target_path: str) -> List[str]:
        reasons = []
        if analysis.get("importance") == "important" or analysis.get("memory_type") in set(self.config.important_memory_types):
            reasons.append("important_memory")
        if analysis.get("action") == "conflict":
            reasons.append("conflict")
        if analysis.get("sensitivity") != "none":
            reasons.append("sensitive_or_uncertain")
        if float(analysis.get("confidence", 0)) < 0.85:
            reasons.append("low_confidence")
        if analysis.get("action") == "update" and not self.storage.content_hash(target_path):
            reasons.append("missing_update_target")
        if self._existing_is_important(target_path):
            reasons.append("target_is_important")
        return list(dict.fromkeys(reasons))

    def _existing_is_important(self, target_path: str) -> bool:
        try:
            text = self.storage.read_text(target_path)
        except NotFoundError:
            return False
        frontmatter, _ = parse_frontmatter(text)
        return str(frontmatter.get("importance", "")).casefold() == "important" or str(frontmatter.get("memory_type", "")).casefold() in set(self.config.important_memory_types)

    def _render_new(self, proposal: dict) -> str:
        analysis = proposal["analysis"]
        now = utc_now()
        frontmatter = render_frontmatter({
            "id": f"bok-{proposal['id']}",
            "title": analysis["title"] or "Bok Memory",
            "memory_type": analysis["memory_type"],
            "importance": analysis["importance"],
            "status": "active",
            "created": now,
            "updated": now,
            "tags": analysis.get("tags", []),
            "source_type": proposal["source"]["type"],
            "source_ref": proposal["source"]["ref"],
            "expires_at": analysis.get("expires_at") or None,
        })
        return (
            f"{frontmatter}# {analysis['title'] or 'Bok Memory'}\n\n"
            f"## 一句话结论\n\n{analysis['summary']}\n\n"
            f"## 保存依据\n\n- 原因：{analysis['reason'] or 'Bok 语义判断'}\n"
            f"- 来源：{proposal['source']['type']} {proposal['source']['ref']}\n"
            f"- 置信度：{analysis['confidence']:.2f}\n\n"
            f"## 后续行动\n\n- 在后续使用中验证并更新这条记忆。\n"
        )

    def _render_update(self, existing: str, proposal: dict) -> str:
        analysis = proposal["analysis"]
        now = utc_now()
        frontmatter, body = parse_frontmatter(existing)
        if frontmatter:
            frontmatter["updated"] = now
            tags = frontmatter.get("tags")
            if not isinstance(tags, list):
                tags = [] if not tags else [str(tags)]
            frontmatter["tags"] = list(dict.fromkeys([str(item) for item in tags] + analysis.get("tags", [])))
            existing = render_frontmatter(frontmatter) + body.lstrip("\r\n")
        section = (
            f"\n\n## Bok 更新记录 {now}\n\n"
            f"- 新判断：{analysis['summary']}\n"
            f"- 更新原因：{analysis['reason'] or 'Bok 语义判断'}\n"
            f"- 来源：{proposal['source']['type']} {proposal['source']['ref']}\n"
            f"- 置信度：{analysis['confidence']:.2f}\n"
        )
        return existing.rstrip() + section + "\n"

    def _commit_locked(self, proposals: List[dict], proposal: dict, *, confirmed_important: bool, reviewer: str) -> dict:
        if proposal.get("status") in {"committed", "auto_committed"}:
            return proposal
        if proposal.get("status") not in {"pending", "ready"}:
            raise ConflictError("Memory proposal is not commit-ready", details={"proposal_id": proposal["id"], "status": proposal.get("status")})
        if proposal.get("requires_review") and not confirmed_important:
            raise PermissionDeniedError(
                "Important, conflicting, sensitive or uncertain memory requires explicit confirmation",
                details={"proposal_id": proposal["id"], "review_reasons": proposal.get("review_reasons", [])},
            )
        target = proposal["target_path"]
        analysis = proposal["analysis"]
        existing_hash = self.storage.content_hash(target)
        if analysis["action"] == "update" and existing_hash:
            content = self._render_update(self.storage.read_text(target), proposal)
            operation = "memory_update"
        else:
            if existing_hash:
                stem = Path(target).stem
                target = str(Path(target).with_name(f"{stem}-{proposal['id'][:8]}.md")).replace("\\", "/")
                proposal["target_path"] = target
            content = self._render_new(proposal)
            operation = "memory_create"
        result = self.storage.write(target, content, expected_hash=existing_hash if operation == "memory_update" else None, operation=operation, metadata={"proposal_id": proposal["id"], "reviewer": reviewer})
        proposal["status"] = "committed" if reviewer != "auto" else "auto_committed"
        proposal["committed_at"] = utc_now()
        proposal["reviewed_by"] = reviewer
        proposal["version_id"] = result.version_id
        proposal["content_hash"] = result.content_hash
        self._mark_quick_note_promoted(proposal)
        self._save(proposals)
        self.search.invalidate()
        return proposal

    def _mark_quick_note_promoted(self, proposal: dict) -> None:
        source = proposal.get("source") or {}
        if source.get("type") != "quick-note" or not source.get("ref"):
            return
        path = str(source["ref"])
        try:
            existing = self.storage.read_text(path)
            existing_hash = self.storage.content_hash(path)
            frontmatter, body = parse_frontmatter(existing)
            if str(frontmatter.get("type", "")) != "quick-note":
                return
            frontmatter["status"] = "promoted"
            frontmatter["promoted_to"] = proposal.get("target_path", "")
            frontmatter["updated"] = utc_now()
            self.storage.write(path, render_frontmatter(frontmatter) + body.lstrip("\r\n"), expected_hash=existing_hash, operation="quick_note_promote", metadata={"proposal_id": proposal["id"]})
        except BokError:
            proposal.setdefault("warnings", []).append("quick_note_status_not_updated")

    def _nearby_for_model(self, material: str) -> List[dict]:
        nearby = self.search.search(material[:800], limit=5, token_budget=1000)["results"]
        return [{"path": item["path"], "heading": item["heading"], "snippet": item["snippet"][:500]} for item in nearby]

    def propose(self, material: str, *, source=None, explicit_cloud_consent: bool = False, _analysis: Optional[dict] = None) -> dict:
        material = str(material or "").strip()
        if not material:
            raise BokError("empty_material", "Memory material cannot be empty")
        if len(material) > 40000:
            raise BokError("material_too_large", "Memory material exceeds the 40,000 character safety limit", status=413)
        material_hash = sha256_text(material)
        with self.lock:
            proposals = self._load()
            for existing in reversed(proposals):
                if existing.get("material_hash") == material_hash and existing.get("status") not in {"rejected", "rolled_back"}:
                    value = dict(existing)
                    value["deduplicated"] = True
                    return value
            analysis = _analysis
            if analysis is None:
                analysis = self.intelligence.analyze(
                    material,
                    nearby=self._nearby_for_model(material),
                    explicit_cloud_consent=explicit_cloud_consent,
                )
            if not isinstance(analysis, dict):
                raise BokError("provider_invalid_batch", "Model did not return an analysis for this capture", status=502)
            if analysis["action"] == "ignore" or not analysis["summary"]:
                return {"status": "ignored", "material_hash": material_hash, "analysis": analysis, "deduplicated": False}
            source_value = self._source_value(source)
            memory_type = str(analysis.get("memory_type", "")).casefold()
            if source_value["type"].casefold() == "conversation" and memory_type in self.PERSONAL_PROFILE_MEMORY_TYPES:
                return {
                    "status": "personal_core_only",
                    "material_hash": material_hash,
                    "memory_type": memory_type,
                    "reason": "personal_profile_is_managed_by_personal_core",
                    "deduplicated": False,
                }
            proposal_id = uuid.uuid4().hex
            target = self._target_path(analysis, proposal_id)
            if analysis["action"] == "create" and self.storage.content_hash(target):
                analysis["action"] = "update"
            reasons = self._review_reasons(analysis, target)
            proposal = {
                "id": proposal_id,
                "created_at": utc_now(),
                "status": "pending",
                "material_hash": material_hash,
                "source": source_value,
                "analysis": analysis,
                "target_path": target,
                "requires_review": bool(reasons),
                "review_reasons": reasons,
                "version_id": "",
            }
            proposals.append(proposal)
            self._save(proposals)
            auto_allowed = (
                not reasons
                and analysis["memory_type"] in set(self.config.auto_commit_memory_types)
                and analysis["action"] in {"create", "update"}
                and analysis["confidence"] >= 0.85
            )
            if auto_allowed:
                return self._commit_locked(proposals, proposal, confirmed_important=False, reviewer="auto")
            return proposal

    def commit(self, proposal_id: str, *, confirm_important: bool = False, reviewer: str = "user") -> dict:
        with self.lock:
            proposals = self._load()
            proposal = next((item for item in proposals if item.get("id") == proposal_id), None)
            if proposal is None:
                raise NotFoundError("Memory proposal does not exist", details={"proposal_id": proposal_id})
            return self._commit_locked(proposals, proposal, confirmed_important=confirm_important, reviewer=reviewer)

    def reject(self, proposal_id: str, *, reason: str = "") -> dict:
        with self.lock:
            proposals = self._load()
            proposal = next((item for item in proposals if item.get("id") == proposal_id), None)
            if proposal is None:
                raise NotFoundError("Memory proposal does not exist", details={"proposal_id": proposal_id})
            if proposal.get("status") in {"committed", "auto_committed"}:
                raise ConflictError("Committed memory must be rolled back instead of rejected")
            proposal["status"] = "rejected"
            proposal["rejected_at"] = utc_now()
            proposal["rejection_reason"] = str(reason)[:500]
            self._save(proposals)
            return proposal

    def rollback(self, proposal_id: str, *, confirm_important: bool = False) -> dict:
        with self.lock:
            proposals = self._load()
            proposal = next((item for item in proposals if item.get("id") == proposal_id), None)
            if proposal is None:
                raise NotFoundError("Memory proposal does not exist", details={"proposal_id": proposal_id})
            if proposal.get("status") not in {"committed", "auto_committed"} or not proposal.get("version_id"):
                raise ConflictError("Memory proposal has no committed version to roll back")
            if proposal.get("requires_review") and not confirm_important:
                raise PermissionDeniedError(
                    "Rolling back an important, conflicting, sensitive or uncertain memory requires explicit confirmation",
                    details={"proposal_id": proposal_id, "review_reasons": proposal.get("review_reasons", [])},
                )
            result = self.storage.rollback(proposal["version_id"])
            proposal["status"] = "rolled_back"
            proposal["rolled_back_at"] = utc_now()
            proposal["rollback_version_id"] = result.version_id
            self._save(proposals)
            self.search.invalidate()
            return proposal

    def counts(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for item in self._load():
            status = str(item.get("status", "unknown"))
            result[status] = result.get(status, 0) + 1
        return result

    def _load_captures(self) -> List[dict]:
        records: Dict[str, dict] = {}
        legacy = read_json(self.capture_path, [])
        if isinstance(legacy, list):
            for item in legacy:
                if isinstance(item, dict) and item.get("id"):
                    records[str(item["id"])] = item
        if self.capture_dir.is_dir():
            for path in self.capture_dir.glob("*.json"):
                item = read_json(path, {})
                if isinstance(item, dict) and item.get("id"):
                    # Sharded records are newer than legacy queue snapshots.
                    records[str(item["id"])] = item
        return sorted(records.values(), key=lambda item: (str(item.get("created_at", "")), str(item.get("id", ""))))

    def _capture_record_path(self, capture_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", capture_id):
            raise BokError("invalid_capture_id", "Capture identifier is invalid")
        return self.capture_dir / f"{capture_id}.json"

    def _capture_hash_path(self, digest: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BokError("invalid_capture_hash", "Capture hash is invalid", status=500)
        return self.capture_hash_dir / digest[:2] / f"{digest}.json"

    def _capture_by_id(self, capture_id: str) -> Optional[dict]:
        value = read_json(self._capture_record_path(capture_id), {})
        if isinstance(value, dict) and value.get("id") == capture_id:
            return value
        legacy = read_json(self.capture_path, [])
        if isinstance(legacy, list):
            for item in legacy:
                if isinstance(item, dict) and item.get("id") == capture_id:
                    return item
        return None

    def _capture_by_hash(self, digest: str) -> Optional[dict]:
        pointer = read_json(self._capture_hash_path(digest), {})
        if isinstance(pointer, dict) and pointer.get("capture_id"):
            item = self._capture_by_id(str(pointer["capture_id"]))
            if item and item.get("material_hash") == digest:
                return item
        # v0.1 compatibility only; new sharded records always have a hash pointer.
        legacy = read_json(self.capture_path, [])
        if isinstance(legacy, list):
            for item in reversed(legacy):
                if isinstance(item, dict) and item.get("material_hash") == digest:
                    return item
        return None

    @staticmethod
    def _time_due(value: str) -> bool:
        if not value:
            return True
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")) <= datetime.now(timezone.utc)
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _retry_at(attempts: int) -> str:
        delay = min(300, 5 * (2 ** min(max(attempts - 1, 0), 6)))
        value = datetime.now(timezone.utc) + timedelta(seconds=delay)
        return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _load_pending_captures(self, *, limit: int) -> List[dict]:
        if not self.capture_pending_dir.is_dir():
            return []
        values = []
        for marker in self.capture_pending_dir.glob("*.json"):
            pointer = read_json(marker, {})
            capture_id = str(pointer.get("capture_id", marker.stem)) if isinstance(pointer, dict) else marker.stem
            if isinstance(pointer, dict) and not self._time_due(str(pointer.get("next_attempt_at", ""))):
                continue
            try:
                item = self._capture_by_id(capture_id)
            except BokError:
                item = None
            if item and item.get("status") in {"queued", "waiting_for_model"}:
                values.append(item)
                if len(values) >= limit:
                    break
            else:
                marker.unlink(missing_ok=True)
        return sorted(values, key=lambda item: (str(item.get("created_at", "")), str(item.get("id", ""))))

    def _save_capture(self, capture: dict) -> None:
        self.storage.ensure_state()
        capture_id = str(capture.get("id", ""))
        record_path = self._capture_record_path(capture_id)
        atomic_write_json(record_path, capture)
        digest = str(capture.get("material_hash", ""))
        if digest:
            atomic_write_json(self._capture_hash_path(digest), {"capture_id": capture_id})
        pending_path = self.capture_pending_dir / f"{capture_id}.json"
        if capture.get("status") in {"queued", "waiting_for_model"}:
            atomic_write_json(
                pending_path,
                {
                    "capture_id": capture_id,
                    "created_at": capture.get("created_at", ""),
                    "next_attempt_at": capture.get("next_attempt_at", ""),
                },
            )
        else:
            pending_path.unlink(missing_ok=True)

    def repair_capture_markers(self) -> dict:
        """Rebuild cheap pending/hash pointers from durable capture records."""
        repaired = 0
        legacy_migrated = 0
        with self.lock:
            for item in self._load_captures():
                capture_id = str(item.get("id", ""))
                digest = str(item.get("material_hash", ""))
                if not re.fullmatch(r"[0-9a-f]{32}", capture_id) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    continue
                hash_path = self._capture_hash_path(digest)
                pending_path = self.capture_pending_dir / f"{capture_id}.json"
                needs_record = not self._capture_record_path(capture_id).is_file()
                needs_hash = not hash_path.is_file()
                needs_pending = item.get("status") in {"queued", "waiting_for_model"} and not pending_path.is_file()
                stale_pending = item.get("status") not in {"queued", "waiting_for_model"} and pending_path.is_file()
                if needs_record or needs_hash or needs_pending or stale_pending:
                    self._save_capture(item)
                    repaired += 1
            legacy = read_json(self.capture_path, [])
            if isinstance(legacy, list) and legacy:
                valid = [
                    item
                    for item in legacy
                    if isinstance(item, dict)
                    and re.fullmatch(r"[0-9a-f]{32}", str(item.get("id", "")))
                    and self._capture_record_path(str(item["id"])).is_file()
                ]
                if len(valid) == len(legacy):
                    legacy_migrated = len(valid)
                    atomic_write_json(self.capture_path, [])
        return {"repaired": repaired, "legacy_migrated": legacy_migrated}

    def capture(self, material: str, *, source=None, explicit_cloud_consent: bool = False) -> dict:
        """Queue first and return immediately; model analysis happens out of band."""
        material = str(material or "").strip()
        if not material:
            raise BokError("empty_material", "Memory material cannot be empty")
        if len(material) > 40000:
            raise BokError("material_too_large", "Memory material exceeds the 40,000 character safety limit", status=413)
        digest = sha256_text(material)
        with self.lock:
            existing = self._capture_by_hash(digest)
            if existing and existing.get("status") not in {"failed", "discarded"}:
                return {key: value for key, value in existing.items() if key not in {"material", "explicit_cloud_consent"}}
            item = {
                "id": uuid.uuid4().hex,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "status": "queued",
                "material_hash": digest,
                "material": material,
                "source": self._source_value(source),
                "explicit_cloud_consent": bool(explicit_cloud_consent),
                "attempts": 0,
                "proposal_id": "",
                "last_error": "",
                "next_attempt_at": "",
            }
            self._save_capture(item)
            return {key: value for key, value in item.items() if key not in {"material", "explicit_cloud_consent"}}

    @staticmethod
    def _age_seconds(value: str) -> float:
        try:
            created = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - created).total_seconds())
        except (TypeError, ValueError):
            return float("inf")

    def process_captures(self, *, limit: int = 3, force: bool = True) -> dict:
        processed = []
        with self.lock:
            bounded_limit = max(1, min(limit, 20))
            candidates = self._load_pending_captures(limit=bounded_limit)
            remaining = sum(1 for _ in self.capture_pending_dir.glob("*.json")) if self.capture_pending_dir.is_dir() else 0
            if (
                not force
                and candidates
                and len(candidates) < min(self.BACKGROUND_BATCH_MIN, bounded_limit)
                and self._age_seconds(str(candidates[0].get("created_at", ""))) < self.BACKGROUND_BATCH_MAX_WAIT_SECONDS
            ):
                wait_seconds = max(
                    1,
                    int(self.BACKGROUND_BATCH_MAX_WAIT_SECONDS - self._age_seconds(str(candidates[0].get("created_at", "")))),
                )
                return {"processed": [], "remaining": remaining, "deferred": len(candidates), "next_batch_in_seconds": wait_seconds}

            groups: Dict[bool, List[dict]] = {False: [], True: []}
            for item in candidates:
                groups[bool(item.get("explicit_cloud_consent"))].append(item)

            for consent, group in groups.items():
                if not group:
                    continue
                entries = []
                preparation_errors: Dict[str, BokError] = {}
                for item in group:
                    capture_id = str(item.get("id", ""))
                    material = str(item.get("material", ""))
                    try:
                        nearby = self._nearby_for_model(material)
                    except BokError as error:
                        preparation_errors[capture_id] = error
                        continue
                    entries.append({"id": capture_id, "material": material, "nearby": nearby})
                if entries:
                    try:
                        analyses = self.intelligence.analyze_many(entries, explicit_cloud_consent=consent)
                    except BokError as error:
                        analyses = {}
                        group_error = error
                    else:
                        group_error = None
                else:
                    analyses = {}
                    group_error = None

                for item in group:
                    item["attempts"] = int(item.get("attempts", 0)) + 1
                    item["updated_at"] = utc_now()
                    try:
                        preparation_error = preparation_errors.get(str(item.get("id", "")))
                        if preparation_error is not None:
                            raise preparation_error
                        if group_error is not None:
                            raise group_error
                        result = self.propose(
                            item.get("material", ""),
                            source=item.get("source"),
                            explicit_cloud_consent=consent,
                            _analysis=analyses.get(str(item.get("id", ""))),
                        )
                        item["status"] = "completed"
                        item["result_status"] = result.get("status", "")
                        item["proposal_id"] = result.get("id", "")
                        item["last_error"] = ""
                        item["next_attempt_at"] = ""
                        item.pop("material", None)
                        item.pop("explicit_cloud_consent", None)
                    except BokError as error:
                        item["status"] = "waiting_for_model" if error.status in {502, 503} or error.code.startswith("provider") or "model" in error.code else "needs_attention"
                        item["last_error"] = error.code
                        item["next_attempt_at"] = self._retry_at(int(item["attempts"])) if item["status"] == "waiting_for_model" else ""
                    self._save_capture(item)
                    processed.append({key: value for key, value in item.items() if key not in {"material", "explicit_cloud_consent"}})
            remaining = sum(1 for _ in self.capture_pending_dir.glob("*.json")) if self.capture_pending_dir.is_dir() else 0
        return {"processed": processed, "remaining": remaining}

    def capture_status(self, capture_id: str = "", *, limit: int = 100) -> dict:
        if capture_id:
            item = self._capture_by_id(capture_id)
            if item is None:
                raise NotFoundError("Capture does not exist", details={"capture_id": capture_id})
            return {key: value for key, value in item.items() if key not in {"material", "explicit_cloud_consent"}}
        captures = self._load_captures()
        public = [{key: value for key, value in item.items() if key not in {"material", "explicit_cloud_consent"}} for item in captures[-max(1, min(limit, 500)):]]
        return {"items": list(reversed(public))}

    def discard_capture(self, capture_id: str, *, reason: str) -> dict:
        """Remove queued raw material while preserving a non-content receipt."""
        with self.lock:
            item = self._capture_by_id(capture_id)
            if item is None:
                raise NotFoundError("Capture does not exist", details={"capture_id": capture_id})
            if item.get("status") == "completed":
                return {key: value for key, value in item.items() if key not in {"material", "explicit_cloud_consent"}}
            item.pop("material", None)
            item.pop("explicit_cloud_consent", None)
            item["status"] = "discarded"
            item["discard_reason"] = re.sub(r"\s+", " ", str(reason or "retention_policy")).strip()[:120]
            item["updated_at"] = utc_now()
            self._save_capture(item)
            return {key: value for key, value in item.items() if key not in {"material", "explicit_cloud_consent"}}

    def forget_capture(self, capture_id: str, *, reason: str = "user_requested_forget") -> dict:
        """Erase capture text and redact proposal state derived from it.

        A committed Markdown document is a separate fact source and is never
        silently deleted here. Its path is returned for an explicit review.
        """
        with self.lock:
            item = self._capture_by_id(capture_id)
            if item is None:
                raise NotFoundError("Capture does not exist", details={"capture_id": capture_id})
            digest = str(item.get("material_hash", ""))
            proposal_id = str(item.get("proposal_id", ""))
            removed_proposal = False
            derived_memory = []
            if proposal_id:
                proposals = self._load()
                kept = []
                for proposal in proposals:
                    if proposal.get("id") != proposal_id:
                        kept.append(proposal)
                        continue
                    if proposal.get("status") in {"committed", "auto_committed", "forgotten_source", "forgotten"}:
                        target_path = str(proposal.get("target_path", ""))
                        version_id = str(proposal.get("version_id", ""))
                        derived_exists = bool(target_path and self.storage.content_hash(target_path))
                        if derived_exists:
                            derived_memory.append(target_path)
                        else:
                            self.storage.forget_activity_references(paths=[target_path], version_ids=[version_id])
                        kept.append({
                            "id": proposal_id,
                            "status": "forgotten_source" if derived_exists else "forgotten",
                            "target_path": target_path if derived_exists else "",
                            "version_id": version_id if derived_exists else "",
                            "created_at": str(proposal.get("created_at", "")),
                            "committed_at": str(proposal.get("committed_at", "")),
                            "forgotten_at": utc_now(),
                            "forget_reason": re.sub(r"\s+", " ", str(reason or "user_requested_forget")).strip()[:120],
                            "requires_derived_memory_review": derived_exists,
                        })
                    else:
                        removed_proposal = True
                if len(kept) != len(proposals):
                    self._save(kept)
                elif kept != proposals:
                    self._save(kept)
            for field in ("material", "material_hash", "explicit_cloud_consent", "next_attempt_at", "last_error"):
                item.pop(field, None)
            item["status"] = "forgotten"
            item["forget_reason"] = re.sub(r"\s+", " ", str(reason or "user_requested_forget")).strip()[:120]
            item["forgotten_at"] = utc_now()
            item["updated_at"] = item["forgotten_at"]
            self.storage.ensure_state()
            atomic_write_json(self._capture_record_path(capture_id), item)
            (self.capture_pending_dir / f"{capture_id}.json").unlink(missing_ok=True)
            if re.fullmatch(r"[0-9a-f]{64}", digest):
                self._capture_hash_path(digest).unlink(missing_ok=True)
            return {
                "capture_id": capture_id,
                "proposal_id": proposal_id,
                "forgotten": True,
                "removed_uncommitted_proposal": removed_proposal,
                "derived_memory_requiring_review": [item for item in derived_memory if item],
            }
