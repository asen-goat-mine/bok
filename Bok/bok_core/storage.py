from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional

from .config import BokConfig
from .errors import BokError, ConflictError, NotFoundError, PermissionDeniedError
from .restore import RestoreChange, TransactionalMarkdownRestore
from .util import InterProcessFileLock, append_jsonl, atomic_write_bytes, atomic_write_json, compact_timestamp, sha256_bytes, tail_text_lines, utc_now


@dataclass
class WriteResult:
    path: str
    content_hash: str
    version_id: str
    created: bool
    operation: str

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "content_hash": self.content_hash,
            "version_id": self.version_id,
            "created": self.created,
            "operation": self.operation,
        }


class VaultStorage:
    """Safe, versioned Markdown writes over a user-owned Vault."""

    def __init__(self, config: BokConfig):
        self.config = config
        self.root = config.vault_root
        self.state = config.state_dir
        self.versions = self.state / "versions"
        self.trash = self.state / "trash"
        self.backups = self.state / "backups"
        self.activity_path = self.state / "activity.jsonl"
        self.lock = InterProcessFileLock(self.state / "write.lock")
        self.restorer = TransactionalMarkdownRestore(
            root=self.root,
            state_dir=self.state,
            lock=self.lock,
            resolve_target=lambda relative: self.resolve(relative, write=True, restore=True),
            current_files=self.markdown_files,
            relative_path=self.relative,
            file_mode=0o644,
            namespace="vault-restore",
        )

    def ensure_state(self) -> None:
        for directory in (self.state, self.versions, self.trash, self.backups, self.state / "cache", self.state / "state"):
            directory.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass

    @staticmethod
    def _normalize(relative: str) -> str:
        if not isinstance(relative, str) or not relative.strip():
            raise BokError("invalid_path", "A non-empty Vault-relative path is required")
        value = relative.replace("\\", "/").strip()
        candidate = PurePosixPath(value)
        if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
            raise PermissionDeniedError("Path traversal or absolute paths are not allowed", details={"path": value})
        normalized = candidate.as_posix()
        if "\x00" in normalized:
            raise PermissionDeniedError("NUL bytes are not allowed in paths")
        return normalized

    def _readable_markdown(self, relative: str) -> str:
        normalized = self._normalize(relative)
        if not normalized.casefold().endswith(".md"):
            raise PermissionDeniedError("Bok only reads Markdown documents through the document API", details={"path": normalized})
        ignored = {item.casefold() for item in self.config.ignored_dirs}
        if any(part.casefold() in ignored for part in PurePosixPath(normalized).parts):
            raise PermissionDeniedError("This directory is private to the runtime or outside the knowledge index", details={"path": normalized})
        return normalized

    def resolve(self, relative: str, *, write: bool = False, must_exist: bool = False, restore: bool = False) -> Path:
        normalized = self._normalize(relative)
        if write:
            if not normalized.casefold().endswith(".md"):
                raise PermissionDeniedError("Bok only writes Markdown documents", details={"path": normalized})
            root_name = normalized.split("/", 1)[0]
            if not restore and root_name not in self.config.allowed_write_roots:
                raise PermissionDeniedError("This directory is not writable by Bok", details={"path": normalized})
        current = self.root
        for part in PurePosixPath(normalized).parts:
            current = current / part
            try:
                if current.is_symlink():
                    raise PermissionDeniedError("Symbolic links are not allowed", details={"path": normalized})
            except OSError as error:
                raise PermissionDeniedError("Path could not be validated", details={"path": normalized}) from error
        try:
            resolved_parent = current.parent.resolve(strict=False)
            resolved_parent.relative_to(self.root)
        except (OSError, RuntimeError, ValueError) as error:
            raise PermissionDeniedError("Path escapes the Vault", details={"path": normalized}) from error
        if must_exist and not current.is_file():
            raise NotFoundError("Markdown document does not exist", details={"path": normalized})
        return current

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def read_bytes(self, relative: str) -> bytes:
        normalized = self._readable_markdown(relative)
        path = self.resolve(normalized, must_exist=True)
        try:
            return path.read_bytes()
        except OSError as error:
            raise BokError("read_failed", "Could not read Markdown document", status=500, details={"path": normalized}) from error

    def read_text(self, relative: str) -> str:
        return self.read_bytes(relative).decode("utf-8-sig", errors="replace")

    def content_hash(self, relative: str) -> Optional[str]:
        try:
            return sha256_bytes(self.read_bytes(relative))
        except NotFoundError:
            return None

    def _new_version(self, relative: str, before: Optional[bytes], after: Optional[bytes], operation: str, metadata: Optional[dict] = None) -> str:
        self.ensure_state()
        version_id = f"{compact_timestamp()}-{uuid.uuid4().hex[:10]}"
        directory = self.versions / version_id
        directory.mkdir(parents=True, exist_ok=False)
        if before is not None:
            atomic_write_bytes(directory / "before.md", before)
        record = {
            "version_id": version_id,
            "path": relative,
            "operation": operation,
            "created_at": utc_now(),
            "before_exists": before is not None,
            "before_hash": sha256_bytes(before) if before is not None else None,
            "after_exists": after is not None,
            "after_hash": sha256_bytes(after) if after is not None else None,
            "metadata": metadata or {},
            "status": "pending",
        }
        atomic_write_json(directory / "meta.json", record)
        return version_id

    def _version_value(self, version_id: str) -> tuple[Path, dict]:
        if not version_id or "/" in version_id or "\\" in version_id or ".." in version_id:
            raise BokError("invalid_version", "Invalid version identifier")
        path = self.versions / version_id / "meta.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise NotFoundError("Version does not exist", details={"version_id": version_id}) from error
        except (OSError, ValueError, TypeError) as error:
            raise BokError("version_corrupt", "Version metadata could not be read", status=500) from error
        if not isinstance(value, dict):
            raise BokError("version_corrupt", "Version metadata could not be read", status=500)
        return path, value

    def _set_version_status(self, version_id: str, status: str, *, error: str = "") -> None:
        path, value = self._version_value(version_id)
        value["status"] = status
        value[f"{status}_at"] = utc_now()
        if error:
            value["last_error"] = str(error)[:160]
        atomic_write_json(path, value)

    def _commit_version(self, version_id: str) -> None:
        self._set_version_status(version_id, "committed")

    def _abort_version(self, version_id: str, error: str) -> None:
        try:
            self._set_version_status(version_id, "aborted", error=error)
        except (BokError, OSError):
            pass

    def repair_versions(self) -> dict:
        """Resolve version journals left pending by an interrupted write."""
        self.ensure_state()
        repaired = {"committed": 0, "aborted": 0, "corrupt": 0}
        with self.lock:
            for directory in self.versions.iterdir():
                meta_path = directory / "meta.json"
                if not directory.is_dir() or directory.is_symlink() or meta_path.is_symlink():
                    repaired["corrupt"] += 1
                    continue
                try:
                    value = json.loads(meta_path.read_text(encoding="utf-8"))
                    if not isinstance(value, dict) or value.get("status", "committed") != "pending":
                        continue
                    relative = self._normalize(str(value.get("path", "")))
                    target = self.resolve(relative, write=True, restore=True)
                    current = target.read_bytes() if target.is_file() else None
                    current_hash = sha256_bytes(current) if current is not None else None
                    after_hash = value.get("after_hash") if value.get("after_exists") else None
                    status = "committed" if current_hash == after_hash else "aborted"
                    value["status"] = status
                    value["repaired_at"] = utc_now()
                    atomic_write_json(meta_path, value)
                    repaired[status] += 1
                except (BokError, OSError, UnicodeError, ValueError, TypeError):
                    repaired["corrupt"] += 1
        return repaired

    def _activity(self, action: str, *, path: str = "", version_id: str = "", details: Optional[dict] = None) -> None:
        self.ensure_state()
        safe_details = details or {}
        record = {
            "at": utc_now(),
            "action": action,
            "path": path,
            "version_id": version_id,
            "details": safe_details,
        }
        append_jsonl(self.activity_path, record)

    def forget_activity_references(self, *, paths=None, version_ids=None) -> dict:
        """Redact content-bearing activity pointers after explicit erasure."""
        forgotten_paths = {str(item) for item in paths or [] if str(item)}
        forgotten_versions = {str(item) for item in version_ids or [] if str(item)}
        if not forgotten_paths and not forgotten_versions:
            return {"redacted": 0}
        redacted = 0
        logs = [self.activity_path, *(self.activity_path.with_name(f"{self.activity_path.name}.{index}") for index in range(1, 4))]
        with self.lock:
            for log in logs:
                if not log.is_file() or log.is_symlink():
                    continue
                output = []
                changed = False
                for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        record = json.loads(line)
                    except ValueError:
                        output.append(line)
                        continue
                    if not isinstance(record, dict) or (
                        str(record.get("path", "")) not in forgotten_paths
                        and str(record.get("version_id", "")) not in forgotten_versions
                    ):
                        output.append(line)
                        continue
                    output.append(json.dumps({
                        "at": str(record.get("at", "")),
                        "action": "forgotten_content",
                        "path": "",
                        "version_id": "",
                        "details": {},
                    }, ensure_ascii=False, separators=(",", ":")))
                    redacted += 1
                    changed = True
                if changed:
                    payload = ("\n".join(output) + ("\n" if output else "")).encode("utf-8")
                    atomic_write_bytes(log, payload, mode=0o600)
        return {"redacted": redacted}

    def write(self, relative: str, text: str, *, expected_hash: Optional[str] = None, operation: str = "write", metadata: Optional[dict] = None) -> WriteResult:
        normalized = self._normalize(relative)
        new_bytes = text.encode("utf-8")
        with self.lock:
            path = self.resolve(normalized, write=True)
            before = path.read_bytes() if path.is_file() else None
            actual_hash = sha256_bytes(before) if before is not None else None
            # None means the caller expects a new document. Check absence under
            # the same lock as the write so concurrent creates cannot overwrite.
            if expected_hash != actual_hash:
                raise ConflictError(
                    "The document changed after it was read",
                    details={"path": normalized, "expected_hash": expected_hash, "actual_hash": actual_hash},
                )
            if before == new_bytes:
                return WriteResult(normalized, sha256_bytes(new_bytes), "", before is None, "unchanged")
            version_id = self._new_version(normalized, before, new_bytes, operation, metadata)
            try:
                atomic_write_bytes(path, new_bytes, mode=0o644)
            except OSError as error:
                self._abort_version(version_id, "write_failed")
                raise BokError("write_failed", "Atomic Markdown write failed", status=500, details={"path": normalized}) from error
            self._commit_version(version_id)
            self._activity(operation, path=normalized, version_id=version_id, details={"before_hash": actual_hash, "after_hash": sha256_bytes(new_bytes)})
            return WriteResult(normalized, sha256_bytes(new_bytes), version_id, before is None, operation)

    def delete(self, relative: str, *, expected_hash: Optional[str] = None) -> dict:
        normalized = self._normalize(relative)
        path = self.resolve(normalized, write=True, must_exist=True)
        with self.lock:
            before = path.read_bytes()
            actual_hash = sha256_bytes(before)
            if expected_hash is not None and expected_hash != actual_hash:
                raise ConflictError("The document changed after it was read", details={"path": normalized, "expected_hash": expected_hash, "actual_hash": actual_hash})
            version_id = self._new_version(normalized, before, None, "trash")
            trash_path = self.trash / version_id / normalized
            trash_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(path), str(trash_path))
            except OSError as error:
                self._abort_version(version_id, "trash_failed")
                raise BokError("trash_failed", "Could not move the document to recoverable trash", status=500, details={"path": normalized}) from error
            self._commit_version(version_id)
            self._activity("trash", path=normalized, version_id=version_id, details={"before_hash": actual_hash})
            return {"path": normalized, "version_id": version_id, "recoverable": True}

    def move(self, source: str, destination: str, *, expected_hash: str) -> dict:
        source_normalized = self._normalize(source)
        destination_normalized = self._normalize(destination)
        with self.lock:
            source_path = self.resolve(source_normalized, write=True, must_exist=True)
            destination_path = self.resolve(destination_normalized, write=True)
            if destination_path.exists():
                raise ConflictError("Move destination already exists", details={"path": destination_normalized})
            data = source_path.read_bytes()
            actual_hash = sha256_bytes(data)
            if not expected_hash or expected_hash != actual_hash:
                raise ConflictError("The source changed after it was read", details={"path": source_normalized, "expected_hash": expected_hash, "actual_hash": actual_hash})
            version_id = self._new_version(destination_normalized, None, data, "move_create", {"moved_from": source_normalized})
            try:
                atomic_write_bytes(destination_path, data, mode=0o644)
            except OSError as error:
                self._abort_version(version_id, "move_create_failed")
                raise BokError("move_failed", "Could not create the move destination", status=500, details={"path": destination_normalized}) from error
            self._commit_version(version_id)
            created = WriteResult(destination_normalized, actual_hash, version_id, True, "move_create")
            try:
                removed = self.delete(source_normalized, expected_hash=actual_hash)
            except Exception:
                try:
                    self.rollback(created.version_id)
                except Exception:
                    pass
                raise
            self._activity("move", path=destination_normalized, version_id=created.version_id, details={"source": source_normalized, "source_version_id": removed["version_id"]})
            return {"source": source_normalized, "destination": destination_normalized, "content_hash": actual_hash, "create_version_id": created.version_id, "source_version_id": removed["version_id"]}

    def version_record(self, version_id: str) -> dict:
        _path, value = self._version_value(version_id)
        status = value.get("status", "committed")
        if status != "committed":
            raise ConflictError("Version is not committed and cannot be used", details={"version_id": version_id, "status": status})
        return value

    def rollback(self, version_id: str) -> WriteResult:
        with self.lock:
            record = self.version_record(version_id)
            relative = str(record["path"])
            path = self.resolve(relative, write=True)
            current = path.read_bytes() if path.is_file() else None
            current_hash = sha256_bytes(current) if current is not None else None
            if current_hash != record.get("after_hash"):
                raise ConflictError(
                    "Rollback refused because the document changed after this version",
                    details={"path": relative, "version_after_hash": record.get("after_hash"), "actual_hash": current_hash},
                )
            before_path = self.versions / version_id / "before.md"
            if record.get("before_exists"):
                try:
                    if before_path.is_symlink():
                        raise ValueError("Version snapshot cannot be a symbolic link")
                    before = before_path.read_bytes()
                    if sha256_bytes(before) != record.get("before_hash"):
                        raise ValueError("Version snapshot hash mismatch")
                    before_text = before.decode("utf-8")
                except (OSError, UnicodeError, ValueError) as error:
                    raise BokError("version_corrupt", "Version snapshot is missing or invalid", status=500, details={"version_id": version_id}) from error
                return self.write(relative, before_text, expected_hash=current_hash, operation="rollback", metadata={"rolled_back_version": version_id})
            rollback_version = self._new_version(relative, current, None, "rollback", {"rolled_back_version": version_id})
            if path.exists():
                trash_path = self.trash / rollback_version / relative
                trash_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(path), str(trash_path))
                except OSError as error:
                    self._abort_version(rollback_version, "rollback_failed")
                    raise BokError("rollback_failed", "Could not rollback the document", status=500, details={"path": relative}) from error
            self._commit_version(rollback_version)
            self._activity("rollback", path=relative, version_id=rollback_version, details={"rolled_back_version": version_id})
            return WriteResult(relative, "", rollback_version, False, "rollback")

    def list_versions(self, relative: Optional[str] = None, limit: int = 100) -> List[dict]:
        self.ensure_state()
        normalized = self._normalize(relative) if relative else ""
        result = []
        for directory in sorted(self.versions.iterdir(), reverse=True):
            if not directory.is_dir():
                continue
            try:
                record = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict) or record.get("status", "committed") != "committed":
                continue
            if normalized and record.get("path") != normalized:
                continue
            result.append(record)
            if len(result) >= max(1, min(limit, 500)):
                break
        return result

    def markdown_files(self) -> Iterable[Path]:
        ignored = set(self.config.ignored_dirs)
        for current, directories, filenames in os.walk(str(self.root)):
            directories[:] = sorted(name for name in directories if name not in ignored and not (Path(current) / name).is_symlink())
            base = Path(current)
            for name in sorted(filenames):
                if not name.casefold().endswith(".md"):
                    continue
                path = base / name
                try:
                    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                        continue
                    path.resolve(strict=True).relative_to(self.root)
                except (OSError, RuntimeError, ValueError):
                    continue
                yield path

    def create_backup(self) -> dict:
        self.ensure_state()
        with self.lock:
            backup_id = f"bok-backup-{compact_timestamp()}-{uuid.uuid4().hex[:8]}"
            destination = self.backups / f"{backup_id}.zip"
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{backup_id}.", suffix=".tmp", dir=str(self.backups))
            os.close(descriptor)
            temporary = Path(temporary_name)
            manifest: Dict[str, str] = {}
            try:
                with zipfile.ZipFile(str(temporary), "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                    for path in self.markdown_files():
                        relative = self.relative(path)
                        data = path.read_bytes()
                        manifest[relative] = sha256_bytes(data)
                        archive.writestr(relative, data)
                    archive.writestr(".bok-backup-manifest.json", json.dumps({"backup_id": backup_id, "created_at": utc_now(), "vault": self.root.name, "files": manifest}, ensure_ascii=False, indent=2))
                os.replace(str(temporary), str(destination))
                try:
                    os.chmod(destination, 0o600)
                except OSError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
            self._activity("backup_created", details={"backup_id": backup_id, "file_count": len(manifest)})
            return {"backup_id": backup_id, "path": str(destination), "file_count": len(manifest), "manifest_hash": sha256_bytes(json.dumps(manifest, sort_keys=True).encode("utf-8"))}

    def _backup_path(self, backup_id: str) -> Path:
        value = str(backup_id or "")
        if not re.fullmatch(r"bok-backup-[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{8}", value):
            raise BokError("invalid_backup_id", "Invalid Bok backup identifier")
        return self.backups / f"{value}.zip"

    def verify_backup(self, backup_id: str) -> dict:
        path = self._backup_path(backup_id)
        if path.is_symlink():
            raise PermissionDeniedError("Backup cannot be a symbolic link")
        if not path.is_file():
            raise NotFoundError("Backup does not exist", details={"backup_id": backup_id})
        errors = []
        try:
            with zipfile.ZipFile(str(path), "r") as archive:
                try:
                    manifest = json.loads(archive.read(".bok-backup-manifest.json"))
                except (KeyError, ValueError, zipfile.BadZipFile) as error:
                    raise BokError("backup_corrupt", "Backup manifest is missing or invalid", status=422) from error
                files = manifest.get("files") if isinstance(manifest, dict) else None
                if not isinstance(files, dict) or manifest.get("backup_id") != backup_id or manifest.get("vault") != self.root.name:
                    raise BokError("backup_corrupt", "Backup manifest does not match this Vault", status=422)
                if len(files) > 50000:
                    raise BokError("backup_too_large", "Backup contains too many files", status=422)
                infos = archive.infolist()
                names = {item.filename: item for item in infos}
                expected_names = set(files) | {".bok-backup-manifest.json"}
                if len(names) != len(infos) or set(names) != expected_names:
                    errors.append({"path": "", "error": "unexpected_or_duplicate_members"})
                total_size = 0
                for relative, expected in files.items():
                    try:
                        normalized = self._normalize(relative)
                        info = names.get(relative)
                        if normalized != relative or not normalized.casefold().endswith(".md") or info is None or info.file_size > 20 * 1024 * 1024:
                            raise ValueError("invalid member")
                        total_size += info.file_size
                        if total_size > 512 * 1024 * 1024 or sha256_bytes(archive.read(relative)) != expected:
                            raise ValueError("invalid content")
                    except (KeyError, OSError, ValueError, PermissionDeniedError):
                        errors.append({"path": str(relative), "error": "invalid_or_missing"})
        except zipfile.BadZipFile as error:
            raise BokError("backup_corrupt", "Backup is not a valid ZIP archive", status=422) from error
        return {"backup_id": backup_id, "valid": not errors, "file_count": len(files), "created_at": manifest.get("created_at", ""), "total_size": total_size, "errors": errors}

    def list_backups(self, *, limit: int = 100) -> dict:
        self.ensure_state()
        items = []
        for path in sorted(self.backups.glob("bok-backup-*.zip"), reverse=True):
            if path.is_symlink():
                continue
            backup_id = path.stem
            try:
                result = self.verify_backup(backup_id)
                items.append({"backup_id": backup_id, "created_at": result["created_at"], "file_count": result["file_count"], "valid": result["valid"]})
            except BokError:
                items.append({"backup_id": backup_id, "created_at": "", "file_count": 0, "valid": False})
            if len(items) >= max(1, min(int(limit or 100), 500)):
                break
        return {"items": items}

    def repair_restore_transactions(self) -> dict:
        return self.restorer.repair_pending()

    def restore_backup(self, backup_id: str, *, confirm_vault: str, mode: str = "exact") -> dict:
        if confirm_vault != self.root.name:
            raise PermissionDeniedError("Backup restore requires the exact Vault name", details={"expected": self.root.name})
        with self.lock:
            verification = self.verify_backup(backup_id)
            if not verification["valid"]:
                raise BokError("backup_corrupt", "Backup verification failed", status=422, details=verification)
            safety = self.create_backup()
            with zipfile.ZipFile(str(self._backup_path(backup_id)), "r") as archive:
                manifest = json.loads(archive.read(".bok-backup-manifest.json"))
                desired = {relative: archive.read(relative) for relative in manifest["files"]}

            def prepare_versions(changes: list[RestoreChange]):
                prepared = []
                try:
                    for change in changes:
                        prepared.append((change.relative, self._new_version(
                            change.relative,
                            change.before,
                            change.after,
                            "backup_restore",
                            {"backup_id": backup_id, "safety_backup": safety["backup_id"], "restore_mode": mode},
                        )))
                    return prepared
                except Exception:
                    for _relative, version_id in prepared:
                        self._abort_version(version_id, "restore_prepare_failed")
                    raise

            def commit_versions(prepared) -> None:
                for _relative, version_id in prepared or []:
                    self._commit_version(version_id)

            def abort_versions(prepared, error: str) -> None:
                for _relative, version_id in prepared or []:
                    self._abort_version(version_id, error)

            result = self.restorer.restore(
                desired,
                mode=mode,
                metadata={"backup_id": backup_id, "safety_backup": safety["backup_id"]},
                prepare_versions=prepare_versions,
                commit_versions=commit_versions,
                abort_versions=abort_versions,
            )
            self._activity("backup_restored", details={
                "backup_id": backup_id,
                "safety_backup": safety["backup_id"],
                "file_count": len(result["restored"]),
                "removed_count": len(result["removed"]),
                "mode": mode,
                "transaction_id": result.get("transaction_id", ""),
            })
            return {"backup_id": backup_id, "safety_backup": safety["backup_id"], **result}

    def recent_activity(self, limit: int = 100) -> List[dict]:
        wanted = max(1, min(limit, 500))
        result = []
        for path in [self.activity_path, *(self.activity_path.with_name(f"{self.activity_path.name}.{index}") for index in range(1, 4))]:
            for line in reversed(tail_text_lines(path, wanted - len(result))):
                try:
                    result.append(json.loads(line))
                except ValueError:
                    continue
                if len(result) >= wanted:
                    return result
        return result
