from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Tuple

from .errors import BokError
from .util import read_json


DEFAULT_WRITE_ROOTS = (
    "01-Inbox",
    "02-Projects",
    "03-Knowledge",
    "04-Content",
    "05-Prompts",
    "06-Business",
    "07-Quick-Notes",
    "90-Archive",
)

DEFAULT_IGNORED_DIRS = (
    ".agents",
    ".bok",
    ".cache",
    ".codex",
    ".git",
    ".mypy_cache",
    ".openai",
    ".pytest_cache",
    ".venv",
    "99-Logs",
    "Bok",
    "AI-Second-Brain-UI",
    "__pycache__",
    "_dist",
    "node_modules",
    "site-packages",
    "venv",
)

DEFAULT_DEFERRED_SEARCH_PREFIXES = (
    ".codebuddy",
    ".workbuddy",
    "02-Projects/model-comparison-benchmark",
    "90-Archive",
    "Self-Media-Lottie-Pack",
    "mario-1-1",
    "mario-clone",
    "platform-game",
    "project",
    "tools",
    "xingya-adventure",
)


@dataclass
class BokConfig:
    vault_root: Path
    host: str = "127.0.0.1"
    port: int = 8771
    local_only: bool = True
    max_context_tokens: int = 2500
    max_search_results: int = 6
    provider: str = "auto"
    provider_model: str = ""
    provider_base_url: str = ""
    provider_api_key_ref: str = ""
    auto_start_local_model: bool = False
    embedding_provider: str = "none"
    embedding_model: str = ""
    deferred_search_prefixes: Tuple[str, ...] = field(default_factory=lambda: DEFAULT_DEFERRED_SEARCH_PREFIXES)
    semantic_full_scan_limit: int = 6000
    embedding_batch_size: int = 32
    conversation_retention_days: int = 14
    personal_core_root: str = ""
    allowed_write_roots: Tuple[str, ...] = field(default_factory=lambda: DEFAULT_WRITE_ROOTS)
    ignored_dirs: Tuple[str, ...] = field(default_factory=lambda: DEFAULT_IGNORED_DIRS)
    important_memory_types: Tuple[str, ...] = (
        "decision",
        "preference",
        "identity",
        "policy",
        "credential",
        "sensitive",
        "conflict",
    )
    auto_commit_memory_types: Tuple[str, ...] = (
        "knowledge",
        "method",
        "project_status",
        "action",
        "reference",
    )

    def __post_init__(self) -> None:
        self.vault_root = self.vault_root.expanduser().resolve()
        if not self.vault_root.is_dir():
            raise BokError("invalid_vault", "Vault root is not a directory", details={"path": str(self.vault_root)})
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError as error:
            raise BokError("invalid_host", "Bok must bind to a loopback IP address") from error
        if not address.is_loopback:
            raise BokError("unsafe_bind", "Bok Memory API may only bind to loopback")
        if not 0 <= self.port <= 65535:
            raise BokError("invalid_port", "Port must be between 0 and 65535")
        if not 256 <= self.max_context_tokens <= 20000:
            raise BokError("invalid_token_budget", "Context budget must be between 256 and 20000 tokens")
        if not 100 <= self.semantic_full_scan_limit <= 50000:
            raise BokError("invalid_semantic_limit", "Semantic full-scan limit must be between 100 and 50000 chunks")
        if not 1 <= self.embedding_batch_size <= 128:
            raise BokError("invalid_embedding_batch", "Embedding batch size must be between 1 and 128")
        if not 1 <= self.conversation_retention_days <= 90:
            raise BokError("invalid_conversation_retention", "Conversation retention must be between 1 and 90 days")
        if self.personal_core_root:
            self.personal_core_root = str(self.validate_personal_core_path(self.personal_core_root))

    def validate_personal_core_path(self, value: str) -> Path:
        raw = Path(str(value or "")).expanduser()
        if not raw.is_absolute():
            raise BokError("invalid_personal_core", "Personal Core path must be absolute")
        resolved = raw.resolve(strict=False)
        if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
            raise BokError("unsafe_personal_core", "Personal Core cannot use a filesystem root or the home directory")
        try:
            resolved.relative_to(self.vault_root)
            raise BokError("unsafe_personal_core", "Personal Core must be outside the project Vault")
        except ValueError:
            pass
        try:
            self.vault_root.relative_to(resolved)
            raise BokError("unsafe_personal_core", "Personal Core cannot contain the project Vault")
        except ValueError:
            pass
        for candidate in (resolved, *resolved.parents):
            if (candidate / ".git").exists():
                raise BokError("unsafe_personal_core", "Personal Core cannot be placed inside a Git repository")
        return resolved

    @property
    def state_dir(self) -> Path:
        return self.vault_root / ".bok"

    @property
    def config_path(self) -> Path:
        return self.state_dir / "config.json"

    @property
    def personal_core_path(self):
        return Path(self.personal_core_root) if self.personal_core_root else None

    @classmethod
    def load(cls, vault_root: Path, overrides: Dict[str, Any] = None) -> "BokConfig":
        root = vault_root.expanduser().resolve()
        data = read_json(root / ".bok" / "config.json", {})
        if not isinstance(data, dict):
            data = {}
        allowed = {
            "host",
            "port",
            "local_only",
            "max_context_tokens",
            "max_search_results",
            "provider",
            "provider_model",
            "provider_base_url",
            "provider_api_key_ref",
            "auto_start_local_model",
            "embedding_provider",
            "embedding_model",
            "deferred_search_prefixes",
            "semantic_full_scan_limit",
            "embedding_batch_size",
            "conversation_retention_days",
            "personal_core_root",
            "allowed_write_roots",
            "ignored_dirs",
            "important_memory_types",
            "auto_commit_memory_types",
        }
        values = {key: value for key, value in data.items() if key in allowed}
        if overrides:
            values.update({key: value for key, value in overrides.items() if value is not None and key in allowed})
        for key in (
            "allowed_write_roots",
            "ignored_dirs",
            "important_memory_types",
            "auto_commit_memory_types",
            "deferred_search_prefixes",
        ):
            if key in values:
                values[key] = tuple(str(item) for item in values[key])
        return cls(vault_root=root, **values)

    def public_dict(self) -> dict:
        return {
            "vault": self.vault_root.name,
            "host": self.host,
            "port": self.port,
            "local_only": self.local_only,
            "max_context_tokens": self.max_context_tokens,
            "max_search_results": self.max_search_results,
            "provider": self.provider,
            "provider_model": self.provider_model,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "deferred_search_prefixes": list(self.deferred_search_prefixes),
            "semantic_full_scan_limit": self.semantic_full_scan_limit,
            "embedding_batch_size": self.embedding_batch_size,
            "conversation_retention_days": self.conversation_retention_days,
            "personal_core_configured": bool(self.personal_core_root),
            "personal_core_name": self.personal_core_path.name if self.personal_core_path else "",
            "auto_start_local_model": self.auto_start_local_model,
            "allowed_write_roots": list(self.allowed_write_roots),
        }
