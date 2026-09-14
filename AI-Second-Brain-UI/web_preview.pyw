from __future__ import annotations

import errno
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


def early_argument_value(name: str) -> str | None:
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError):
        return None


def configured_path(argument: str, environment: str, fallback: Path) -> Path:
    raw = early_argument_value(argument) or os.environ.get(environment, "")
    return Path(raw).expanduser().resolve() if raw else fallback.resolve()


def vault_selection_file() -> Path:
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "Boujoy" / "BoujoyKnowledge" / "vault.json"


def saved_vault_root(fallback: Path, *, selection_file: Path | None = None) -> Path:
    # Imports used by the contract tests must remain hermetic. Persisted user
    # state is only consulted by the real preview process.
    if __name__ != "__main__" and selection_file is None:
        return fallback.resolve()
    target = selection_file or vault_selection_file()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        selected = Path(str(payload.get("vault", ""))).expanduser().resolve()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return fallback.resolve()
    return selected if selected.is_dir() else fallback.resolve()


def save_vault_root(vault: Path, *, selection_file: Path | None = None) -> None:
    selected = vault.expanduser().resolve(strict=True)
    if not selected.is_dir():
        raise ValueError("Selected Vault is not a directory.")
    target = selection_file or vault_selection_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps({"vault": str(selected)}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(target)


SCRIPT_DIR = Path(__file__).resolve().parent
APP_DIR = configured_path("--ui-root", "BOK_UI_ROOT", SCRIPT_DIR)
VAULT_ROOT = configured_path(
    "--vault-root",
    "BOK_VAULT_ROOT",
    saved_vault_root(APP_DIR.parent),
)
BOK_PACKAGE_ROOT = configured_path(
    "--bok-package-root",
    "BOK_PACKAGE_ROOT",
    APP_DIR.parent / "Bok",
)
native_control_value = early_argument_value("--native-control-dir") or os.environ.get(
    "BOK_NATIVE_CONTROL_DIR", ""
)
NATIVE_CONTROL_DIR = (
    Path(native_control_value).expanduser().resolve() if native_control_value else None
)
if str(BOK_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BOK_PACKAGE_ROOT))

from bok_core.ui_bridge import BokUIBridge


SERVICE_ID = "boujoy-knowledge-preview"
SERVICE_VERSION = 6
DEFAULT_MACOS_PORT = 8765
IDLE_TIMEOUT_SECONDS = 10 * 60
HEARTBEAT_TIMEOUT_SECONDS = 0.75
INITIAL_MARKDOWN_TEXT_BYTES = 128 * 1024
RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)$")
PROTECTED_MARKDOWN_ROOTS = ("00-system/", "ai-second-brain-ui/", "98-skills/", "99-logs/")
PROTECTED_MARKDOWN_FILES = {"agents.md", "dashboard.md", "readme.md"}

IGNORED_DIRS = {
    ".bok",
    ".cache",
    ".codebuddy",
    ".codex",
    ".git",
    ".agents",
    ".mypy_cache",
    ".nox",
    ".openai",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".workbuddy",
    "__pypackages__",
    "node_modules",
    "site-packages",
    "venv",
    "__pycache__",
    "99-Logs",
    "_dist",
    "target",
    "build-resources",
}


def relative_display(path: Path | str) -> str:
    try:
        return Path(path).resolve(strict=False).relative_to(VAULT_ROOT).as_posix()
    except (OSError, RuntimeError, ValueError):
        return Path(path).name or "."


def diagnostic(path: Path | str, error: BaseException) -> dict[str, str]:
    return {
        "path": relative_display(path),
        "error": f"{type(error).__name__}: {error}",
    }


def is_protected_markdown_path(relative: str) -> bool:
    normalized = relative.replace("\\", "/").lower()
    return normalized in PROTECTED_MARKDOWN_FILES or normalized.startswith(
        PROTECTED_MARKDOWN_ROOTS
    )


class VaultCache:
    def __init__(self) -> None:
        self.lock = Lock()
        self.source_fingerprint = ""
        self.etag = ""
        self.payload = b""
        self.last_checked = 0.0
        self.last_verified = 0.0
        self.entries: dict[str, tuple[tuple[int, int, int, int], dict]] = {}

    @staticmethod
    def markdown_paths() -> tuple[list[Path], list[dict[str, str]]]:
        if not VAULT_ROOT.is_dir():
            raise RuntimeError(f"Vault root is not a directory: {VAULT_ROOT}")

        paths: list[Path] = []
        skipped: list[dict[str, str]] = []

        def on_walk_error(error: OSError) -> None:
            skipped.append(diagnostic(error.filename or VAULT_ROOT, error))

        for current, directories, filenames in os.walk(
            VAULT_ROOT,
            onerror=on_walk_error,
        ):
            directories[:] = sorted(
                name for name in directories if name not in IGNORED_DIRS
            )
            current_path = Path(current)
            for filename in sorted(filenames):
                if filename.lower().endswith(".md"):
                    paths.append(current_path / filename)
        return paths, skipped

    def read(self, *, max_age: float = 0.0) -> tuple[str, bytes]:
        # Coalesce simultaneous refreshes; a change to one card must not hash
        # and decode every other document again. Direct reads remain fresh.
        with self.lock:
            if self.payload and time.monotonic() - self.last_checked < max_age:
                return self.etag, self.payload
            result = self._read_locked()
            self.last_checked = time.monotonic()
            return result

    def _read_locked(self) -> tuple[str, bytes]:
        paths, skipped = self.markdown_paths()
        snapshots: list[tuple[Path, os.stat_result]] = []
        parts: list[str] = []
        for path in paths:
            try:
                stat = path.stat()
                path.resolve(strict=True).relative_to(VAULT_ROOT)
                relative = path.relative_to(VAULT_ROOT).as_posix()
            except (OSError, ValueError) as error:
                skipped.append(diagnostic(path, error))
                continue
            snapshots.append((path, stat))
            parts.append(f"{relative}:{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_size}:{stat.st_ino}")

        for item in skipped:
            parts.append(f"skipped:{item['path']}:{item['error']}")
        source_fingerprint = hashlib.sha256(
            "|".join(parts).encode("utf-8")
        ).hexdigest()
        # Windows ctime is creation time. Periodically verify bytes as well,
        # so editors preserving size/mtime cannot keep stale content forever.
        verify_content = time.monotonic() - self.last_verified >= 30
        if not verify_content and source_fingerprint == self.source_fingerprint and self.payload:
            return self.etag, self.payload

        files = []
        entries = {}
        unreadable: list[dict[str, str]] = []
        for path, stat in snapshots:
            try:
                relative = path.relative_to(VAULT_ROOT).as_posix()
                signature = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
                cached = self.entries.get(relative)
                if not verify_content and cached and cached[0] == signature:
                    files.append(cached[1])
                    entries[relative] = cached
                    continue
                digest = hashlib.sha256()
                initial = bytearray()
                with path.open("rb") as source:
                    while True:
                        chunk = source.read(128 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        if len(initial) < INITIAL_MARKDOWN_TEXT_BYTES:
                            remaining = INITIAL_MARKDOWN_TEXT_BYTES - len(initial)
                            initial.extend(chunk[:remaining])
                text = bytes(initial).decode("utf-8", errors="replace")
            except (OSError, ValueError) as error:
                unreadable.append(diagnostic(path, error))
                continue
            files.append(
                {
                    "path": relative,
                    "text": text,
                    "lastModified": stat.st_mtime * 1000,
                    "size": stat.st_size,
                    "truncated": stat.st_size > INITIAL_MARKDOWN_TEXT_BYTES,
                    "contentHash": digest.hexdigest(),
                }
            )
            entries[relative] = (signature, files[-1])

        payload = json.dumps(
            {
                "root": VAULT_ROOT.name,
                "files": files,
                "skipped": skipped,
                "unreadable": unreadable,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        etag = hashlib.sha256(payload).hexdigest()
        # A failed open is not a complete scan even if the file's stat stays
        # unchanged. Retry missing entries next time; successful entries remain
        # reusable so one temporarily locked file never forces a full reread.
        self.source_fingerprint = "" if unreadable else source_fingerprint
        self.etag = etag
        self.payload = payload
        self.entries = entries
        if verify_content:
            self.last_verified = time.monotonic()
        return etag, payload


CACHE = VaultCache()


def native_folder_picker_available() -> bool:
    if sys.platform == "darwin":
        return Path("/usr/bin/osascript").is_file()
    if sys.platform == "win32":
        return bool(shutil.which("powershell.exe") or shutil.which("pwsh.exe"))
    return False


def choose_native_vault() -> Path | None:
    if sys.platform == "darwin":
        command = [
            "/usr/bin/osascript",
            "-e",
            'tell application "System Events" to activate',
            "-e",
            'set selectedFolder to choose folder with prompt "选择 Boujoy Markdown 知识库文件夹"',
            "-e",
            "POSIX path of selectedFolder",
        ]
    elif sys.platform == "win32":
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if not powershell:
            raise RuntimeError("Windows folder picker is unavailable.")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$dialog=New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$dialog.Description='选择 Boujoy Markdown 知识库文件夹';"
            "$dialog.ShowNewFolderButton=$false;"
            "if($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){"
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "Write-Output $dialog.SelectedPath}"
        )
        command = [powershell, "-NoProfile", "-STA", "-Command", script]
    else:
        raise RuntimeError("Native folder selection is unavailable on this platform.")

    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
        check=False,
    )
    output = completed.stdout.strip()
    if completed.returncode != 0:
        detail = completed.stderr.strip()
        if not output and (not detail or "canceled" in detail.lower() or "-128" in detail):
            return None
        raise RuntimeError(detail or "Folder selection failed.")
    if not output:
        return None
    selected = Path(output.splitlines()[-1]).expanduser().resolve(strict=True)
    if not selected.is_dir():
        raise ValueError("Selected Vault is not a directory.")
    return selected


def launch_selected_vault(selected: Path) -> str:
    handshake_dir = Path(tempfile.mkdtemp(prefix="boujoy-vault-switch-"))
    ready_file = handshake_dir / "ready"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--server-only",
        "0",
        "--ui-root",
        str(APP_DIR),
        "--vault-root",
        str(selected),
        "--bok-package-root",
        str(BOK_PACKAGE_ROOT),
        "--ready-file",
        str(ready_file),
    ]
    options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(command, **options)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if ready_file.is_file():
                url = ready_file.read_text(encoding="utf-8").strip()
                parsed = urlparse(url)
                if parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port:
                    return url.rstrip("/") + "/"
                raise RuntimeError("The selected Vault returned an invalid local URL.")
            if process.poll() is not None:
                raise RuntimeError("The selected Vault service did not start.")
            time.sleep(0.05)
        raise RuntimeError("Timed out while switching the Vault.")
    finally:
        try:
            ready_file.unlink(missing_ok=True)
            handshake_dir.rmdir()
        except OSError:
            pass


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int]) -> None:
        self.bok_bridge = BokUIBridge(VAULT_ROOT)
        super().__init__(address, PreviewHandler)
        self.last_request = time.monotonic()
        self.started_at = time.time()

    def server_bind(self) -> None:
        # This service binds to a numeric loopback address. HTTPServer's reverse
        # DNS lookup adds no useful identity and can stall offline startup.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def server_close(self) -> None:
        self.bok_bridge.close()
        super().server_close()


class PreviewHandler(SimpleHTTPRequestHandler):
    server: PreviewServer

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(APP_DIR), **kwargs)

    def log_message(self, _format: str, *_args) -> None:
        return

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "media-src 'self' blob:; connect-src 'self'; font-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        self.server.last_request = time.monotonic()
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/api/vault":
            self.serve_vault()
            return
        if route == "/api/heartbeat":
            self.serve_heartbeat()
            return
        if route == "/api/cleanup":
            self.serve_cleanup_status()
            return
        if route == "/api/file":
            self.serve_file(parsed.query, head_only=False)
            return
        if route.startswith("/api/bok/"):
            self.serve_bok_bridge("GET")
            return
        if route == "/api/reveal":
            self.method_not_allowed("POST")
            return
        if route == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_HEAD(self) -> None:
        self.server.last_request = time.monotonic()
        parsed = urlparse(self.path)
        if parsed.path == "/api/file":
            self.serve_file(parsed.query, head_only=True)
            return
        if parsed.path == "/api/heartbeat":
            self.serve_heartbeat(head_only=True)
            return
        if parsed.path == "/api/reveal":
            self.method_not_allowed("POST", head_only=True)
            return
        if parsed.path.startswith("/api/bok/"):
            self.method_not_allowed("GET, POST", head_only=True)
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_HEAD()

    def do_POST(self) -> None:
        self.server.last_request = time.monotonic()
        parsed = urlparse(self.path)
        if parsed.path == "/api/native/quick-note":
            self.serve_native_quick_note()
            return
        if parsed.path == "/api/native/select-vault":
            self.serve_native_select_vault()
            return
        if parsed.path == "/api/native/connect-codex":
            self.serve_native_connect_codex()
            return
        if parsed.path == "/api/reveal":
            self.serve_reveal(parsed.query)
            return
        if parsed.path == "/api/cleanup":
            self.serve_cleanup()
            return
        if parsed.path == "/api/delete-card":
            self.serve_delete_card()
            return
        if parsed.path.startswith("/api/bok/"):
            self.serve_bok_bridge("POST")
            return
        self.method_not_allowed("GET, HEAD")

    def send_json(
        self,
        status: int,
        value: object,
        *,
        head_only: bool = False,
    ) -> None:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def method_not_allowed(self, allow: str, *, head_only: bool = False) -> None:
        payload = b'{"error":"method_not_allowed"}'
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", allow)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def serve_bok_bridge(self, method: str) -> None:
        if not self.api_request_is_local(require_browser_proof=method == "POST"):
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "Request origin is not local."})
            return
        body = b""
        if method == "POST":
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length header."})
                return
            if length < 0 or length > 1024 * 1024:
                self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Request exceeds the 1 MiB limit."})
                return
            body = self.rfile.read(length) if length else b""
        response = self.server.bok_bridge.forward(
            method,
            self.path,
            body=body,
            headers={
                "Content-Type": self.headers.get("Content-Type", ""),
                "Idempotency-Key": self.headers.get("Idempotency-Key", ""),
            },
        )
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        self.wfile.write(response.body)

    def serve_native_quick_note(self) -> None:
        if not self.api_request_is_local(require_browser_proof=True):
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "Request origin is not local."})
            return
        if NATIVE_CONTROL_DIR is None:
            self.send_json(HTTPStatus.OK, {"native": False})
            return
        try:
            NATIVE_CONTROL_DIR.mkdir(parents=True, exist_ok=True)
            request_path = NATIVE_CONTROL_DIR / "open-quick-note.request"
            temporary = NATIVE_CONTROL_DIR / f"open-quick-note.{uuid.uuid4().hex}.tmp"
            temporary.write_text(f"{time.time_ns()}\n", encoding="utf-8")
            temporary.replace(request_path)
        except OSError:
            self.send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "native_window_signal_failed"},
            )
            return
        self.send_json(HTTPStatus.ACCEPTED, {"native": True})

    def serve_native_select_vault(self) -> None:
        if not self.api_request_is_local(require_browser_proof=True):
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "Request origin is not local."})
            return
        if NATIVE_CONTROL_DIR is None:
            if not native_folder_picker_available():
                self.send_json(HTTPStatus.OK, {"native": False})
                return
            try:
                selected = choose_native_vault()
                if selected is None:
                    self.send_json(
                        HTTPStatus.OK,
                        {"native": True, "cancelled": True},
                    )
                    return
                save_vault_root(selected)
                if selected == VAULT_ROOT:
                    host, port = self.server.server_address
                    url = f"http://{host}:{port}/"
                else:
                    url = launch_selected_vault(selected)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                self.send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "native_folder_selection_failed", "detail": str(error)},
                )
                return
            self.send_json(
                HTTPStatus.OK,
                {"native": True, "url": url, "vaultName": selected.name},
            )
            return
        try:
            NATIVE_CONTROL_DIR.mkdir(parents=True, exist_ok=True)
            request_path = NATIVE_CONTROL_DIR / "select-vault.request"
            temporary = NATIVE_CONTROL_DIR / f"select-vault.{uuid.uuid4().hex}.tmp"
            temporary.write_text(f"{time.time_ns()}\n", encoding="utf-8")
            temporary.replace(request_path)
        except OSError:
            self.send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "native_folder_signal_failed"},
            )
            return
        self.send_json(HTTPStatus.ACCEPTED, {"native": True})

    def serve_native_connect_codex(self) -> None:
        if not self.api_request_is_local(require_browser_proof=True):
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "Request origin is not local."})
            return
        if NATIVE_CONTROL_DIR is None:
            self.send_json(HTTPStatus.OK, {"native": False, "status": "browser"})
            return
        result = connect_codex_mcp()
        status = HTTPStatus.OK if result.get("ok") else HTTPStatus.SERVICE_UNAVAILABLE
        self.send_json(status, {"native": True, **result})

    def local_header_url(self, value: str) -> bool:
        parsed = urlparse(value)
        request_host = urlparse(f"//{self.headers.get('Host', '')}")
        try:
            parsed_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            request_port = request_host.port or 80
        except ValueError:
            return False
        return bool(
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost"}
            and parsed.hostname == request_host.hostname
            and parsed_port == request_port
        )

    def api_request_is_local(self, *, require_browser_proof: bool = False) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        if host not in {"127.0.0.1", "localhost"}:
            return False

        fetch_site = self.headers.get("Sec-Fetch-Site", "").lower()
        if fetch_site and fetch_site not in {"same-origin", "none"}:
            return False

        local_context_header = False
        for header_name in ("Origin", "Referer"):
            value = self.headers.get(header_name)
            if not value:
                continue
            if not self.local_header_url(value):
                return False
            local_context_header = True

        if require_browser_proof:
            return fetch_site in {"same-origin", "none"} and local_context_header
        return True

    def requested_vault_path(
        self,
        query: str,
        *,
        require_browser_proof: bool = False,
        reject_symlinks: bool = False,
    ) -> tuple[Path, str]:
        if not self.api_request_is_local(
            require_browser_proof=require_browser_proof
        ):
            raise PermissionError("Request origin is not local.")
        values = parse_qs(query, keep_blank_values=True).get("path", [])
        if len(values) != 1 or not values[0] or "\x00" in values[0]:
            raise ValueError("Exactly one non-empty path is required.")
        raw_path = values[0]
        lexical_candidate = Path(os.path.abspath(VAULT_ROOT / raw_path))
        try:
            lexical_relative = lexical_candidate.relative_to(VAULT_ROOT)
        except ValueError as error:
            raise PermissionError("Path is outside the Vault.") from error
        if reject_symlinks:
            current = VAULT_ROOT
            for part in lexical_relative.parts:
                current /= part
                if current.is_symlink():
                    raise PermissionError("Symlink paths cannot be changed from the preview.")
        candidate = (VAULT_ROOT / raw_path).resolve(strict=True)
        try:
            relative = candidate.relative_to(VAULT_ROOT).as_posix()
        except ValueError as error:
            raise PermissionError("Path is outside the Vault.") from error
        return candidate, relative

    def serve_heartbeat(self, *, head_only: bool = False) -> None:
        if not self.api_request_is_local():
            self.send_json(
                HTTPStatus.FORBIDDEN,
                {"error": "Request origin is not local."},
                head_only=head_only,
            )
            return
        host, port = self.server.server_address
        self.send_json(
            HTTPStatus.OK,
            {
                "service": SERVICE_ID,
                "version": SERVICE_VERSION,
                "ready": True,
                "indexReady": bool(CACHE.payload),
                "vaultRoot": str(VAULT_ROOT),
                "nativeShell": NATIVE_CONTROL_DIR is not None,
                "nativeFolderPicker": native_folder_picker_available(),
                "url": f"http://{host}:{port}/",
                "pid": os.getpid(),
                "startedAt": self.server.started_at,
            },
            head_only=head_only,
        )

    def serve_vault(self) -> None:
        if not self.api_request_is_local():
            self.send_json(
                HTTPStatus.FORBIDDEN,
                {"error": "Request origin is not local."},
            )
            return
        try:
            etag, payload = CACHE.read(max_age=0.5)
        except Exception as error:
            self.send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "vault_scan_failed", "detail": str(error)},
            )
            return
        if self.headers.get("If-None-Match") == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(payload)

    def serve_reveal(self, query: str) -> None:
        try:
            candidate, relative = self.requested_vault_path(
                query,
                require_browser_proof=True,
            )
        except ValueError as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except FileNotFoundError:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Path does not exist."})
            return
        except PermissionError as error:
            self.send_json(HTTPStatus.FORBIDDEN, {"error": str(error)})
            return
        except (OSError, RuntimeError):
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Path is unavailable."})
            return

        if sys.platform == "darwin":
            command = ["/usr/bin/open", "-R", str(candidate)]
        elif sys.platform == "win32":
            command = ["explorer.exe", f"/select,{candidate}"]
        else:
            self.send_json(
                HTTPStatus.NOT_IMPLEMENTED,
                {"error": "Reveal is supported on macOS and Windows only."},
            )
            return
        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            self.send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "reveal_failed", "detail": str(error)},
            )
            return
        self.send_json(HTTPStatus.OK, {"revealed": relative})

    @staticmethod
    def cleanup_candidates() -> set[str]:
        report = VAULT_ROOT / "00-System" / "Cleanup-Candidates.md"
        if not report.is_file():
            return set()
        text = report.read_text(encoding="utf-8", errors="replace")
        text = re.split(r"^## E\. 完全重复的 Markdown\s*$", text, maxsplit=1, flags=re.MULTILINE)[0]
        candidates = set()
        for value in re.findall(r"`([^`]+)`", text):
            relative = value.strip().replace("\\", "/")
            relative_path = Path(relative)
            if (
                relative
                and not relative_path.is_absolute()
                and ".." not in relative_path.parts
                and not is_protected_markdown_path(relative)
                and not relative.startswith("tools/")
            ):
                candidates.add(relative)
        return candidates

    @classmethod
    def cleanup_status(cls) -> dict:
        existing = []
        already_absent = []
        blocked = []
        for relative in sorted(cls.cleanup_candidates()):
            candidate = VAULT_ROOT / relative
            if not candidate.exists():
                already_absent.append(relative)
                continue
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(VAULT_ROOT)
                current = VAULT_ROOT
                for part in Path(relative).parts:
                    current /= part
                    if current.is_symlink():
                        raise PermissionError("symlink")
            except (OSError, RuntimeError, ValueError, PermissionError):
                blocked.append(relative)
                continue
            existing.append(relative)
        return {
            "items": existing,
            "count": len(existing),
            "already_absent": already_absent,
            "already_absent_count": len(already_absent),
            "blocked": blocked,
            "blocked_count": len(blocked),
            "verified": not blocked,
        }

    def serve_cleanup_status(self) -> None:
        if not self.api_request_is_local():
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "Request origin is not local."})
            return
        self.send_json(HTTPStatus.OK, self.cleanup_status())

    @staticmethod
    def move_to_trash(paths: list[Path]) -> None:
        if not paths:
            return
        if sys.platform == "darwin":
            trash = Path.home() / ".Trash"
            trash.mkdir(exist_ok=True)
            for path in paths:
                if not path.exists():
                    raise FileNotFoundError(str(path))
                destination = trash / path.name
                if destination.exists():
                    destination = trash / f"{path.stem} {uuid.uuid4().hex[:8]}{path.suffix}"
                shutil.move(str(path), str(destination))
            return
        if sys.platform == "win32":
            for path in paths:
                command = (
                    "Add-Type -AssemblyName Microsoft.VisualBasic; "
                    f"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('{str(path).replace(chr(39), chr(39) + chr(39))}', "
                    "'OnlyErrorDialogs', 'SendToRecycleBin')"
                )
                subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], check=True, timeout=30)
            return
        completed = subprocess.run(["gio", "trash", *[str(path) for path in paths]], check=False, timeout=60)
        if completed.returncode != 0:
            raise RuntimeError("系统不支持移入废纸篓")

    def serve_cleanup(self) -> None:
        if not self.api_request_is_local(require_browser_proof=True):
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "Request origin is not local."})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > 2_000_000:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid cleanup request."})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Cleanup request must be an object.")
            requested = payload.get("paths", [])
            if not isinstance(requested, list) or not all(isinstance(item, str) for item in requested):
                raise ValueError("Paths must be a list.")
            allowed = self.cleanup_candidates()
            if any(item.replace("\\", "/") not in allowed for item in requested):
                raise PermissionError("Only reported cleanup candidates can be moved.")
            moved = []
            already_absent = []
            failed = []
            attempted = []
            for relative in dict.fromkeys(item.replace("\\", "/") for item in requested):
                raw_candidate = VAULT_ROOT / relative
                if not raw_candidate.exists():
                    already_absent.append(relative)
                    continue
                try:
                    candidate, normalized = self.requested_vault_path(
                        urlencode({"path": relative}),
                        require_browser_proof=True,
                        reject_symlinks=True,
                    )
                    if is_protected_markdown_path(normalized):
                        raise PermissionError("System files, indexes, UI and Skills are protected.")
                    attempted.append((relative, candidate))
                    self.move_to_trash([candidate])
                    if candidate.exists():
                        raise RuntimeError("Path still exists after the trash operation")
                    moved.append(relative)
                except PermissionError:
                    raise
                except (FileNotFoundError, OSError, RuntimeError, subprocess.SubprocessError) as error:
                    failed.append({"path": relative, "detail": str(error) or type(error).__name__})
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except PermissionError as error:
            self.send_json(HTTPStatus.FORBIDDEN, {"error": str(error)})
            return
        remaining = [relative for relative, candidate in attempted if candidate.exists()]
        result = {
            "moved": len(moved),
            "moved_paths": moved,
            "already_absent": len(already_absent),
            "already_absent_paths": already_absent,
            "failed": failed,
            "remaining": remaining,
            "verified": not failed and not remaining,
        }
        if failed or remaining:
            result["error"] = "cleanup_incomplete"
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, result)
            return
        self.send_json(HTTPStatus.OK, result)

    def serve_delete_card(self) -> None:
        if not self.api_request_is_local(require_browser_proof=True):
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "Request origin is not local."})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > 64_000:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid delete request."})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Delete request must be an object.")
            relative = str(payload.get("path", "")).replace("\\", "/")
            if not relative.lower().endswith(".md"):
                raise PermissionError("System files, indexes, UI and Skills are protected.")
            candidate, normalized = self.requested_vault_path(
                urlencode({"path": relative}),
                require_browser_proof=True,
                reject_symlinks=True,
            )
            if is_protected_markdown_path(normalized):
                raise PermissionError("System files, indexes, UI and Skills are protected.")
            self.move_to_trash([candidate])
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except PermissionError as error:
            self.send_json(HTTPStatus.FORBIDDEN, {"error": str(error)})
            return
        except (FileNotFoundError, OSError, RuntimeError, subprocess.SubprocessError) as error:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "delete_failed", "detail": str(error) or type(error).__name__})
            return
        self.send_json(HTTPStatus.OK, {"deleted": normalized})

    def requested_range(self, size: int) -> tuple[int, int] | None:
        header = self.headers.get("Range")
        if not header:
            return None
        if "," in header:
            raise ValueError("Multiple ranges are not supported.")
        match = RANGE_PATTERN.fullmatch(header.strip())
        if not match or size <= 0:
            raise ValueError("Invalid byte range.")
        start_text, end_text = match.groups()
        if not start_text and not end_text:
            raise ValueError("Invalid byte range.")
        if not start_text:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                raise ValueError("Invalid byte range.")
            start = max(0, size - suffix_length)
            end = size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
            if start >= size or start > end:
                raise ValueError("Range is outside the file.")
            end = min(end, size - 1)
        return start, end

    def serve_file(self, query: str, *, head_only: bool) -> None:
        try:
            candidate, _relative = self.requested_vault_path(query)
        except ValueError as error:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": str(error)},
                head_only=head_only,
            )
            return
        except FileNotFoundError:
            self.send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "File does not exist."},
                head_only=head_only,
            )
            return
        except PermissionError as error:
            self.send_json(
                HTTPStatus.FORBIDDEN,
                {"error": str(error)},
                head_only=head_only,
            )
            return
        except (OSError, RuntimeError):
            self.send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "File is unavailable."},
                head_only=head_only,
            )
            return

        if not candidate.is_file():
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Path is not a file."},
                head_only=head_only,
            )
            return
        try:
            source = candidate.open("rb")
            size = os.fstat(source.fileno()).st_size
            byte_range = self.requested_range(size)
        except (OSError, ValueError) as error:
            if "source" in locals():
                source.close()
            if isinstance(error, ValueError):
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self.send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "file_stat_failed", "detail": str(error)},
                    head_only=head_only,
                )
            return

        start, end = byte_range or (0, max(0, size - 1))
        length = 0 if size == 0 else end - start + 1
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(
            HTTPStatus.PARTIAL_CONTENT if byte_range else HTTPStatus.OK
        )
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header(
            "Content-Security-Policy",
            "sandbox; default-src 'none'; style-src 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self'",
        )
        if byte_range:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head_only or length == 0:
            source.close()
            return

        try:
            with source:
                source.seek(start)
                remaining = length
                while remaining:
                    chunk = source.read(min(128 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return
        except OSError:
            return


def browser_candidates() -> list[Path]:
    candidates = []
    if sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            ]
        )
    for environment, suffix in (
        ("ProgramFiles(x86)", "Microsoft/Edge/Application/msedge.exe"),
        ("ProgramFiles", "Microsoft/Edge/Application/msedge.exe"),
        ("ProgramFiles", "Google/Chrome/Application/chrome.exe"),
        ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
    ):
        base = os.environ.get(environment)
        if base:
            candidates.append(Path(base) / Path(suffix))
    return candidates


def open_app_window(url: str) -> bool:
    for browser in browser_candidates():
        if browser.exists():
            if sys.platform == "darwin":
                try:
                    completed = subprocess.run(
                        [
                            "/usr/bin/open",
                            "-n",
                            "-a",
                            str(browser.parents[2]),
                            "--args",
                            f"--app={url}",
                            "--new-window",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=8,
                        check=False,
                    )
                except (OSError, subprocess.SubprocessError):
                    continue
                if completed.returncode == 0:
                    return True
                continue
            subprocess.Popen(
                [str(browser), f"--app={url}", "--new-window"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
    return webbrowser.open(url, new=1)


def argument_value(name: str) -> str | None:
    return early_argument_value(name)


def matching_preview_url(host: str, port: int) -> str | None:
    request = Request(
        f"http://{host}:{port}/api/heartbeat",
        headers={"Host": f"{host}:{port}"},
    )
    try:
        with urlopen(request, timeout=HEARTBEAT_TIMEOUT_SECONDS) as response:
            if response.status != HTTPStatus.OK:
                return None
            heartbeat = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    if (
        heartbeat.get("service") != SERVICE_ID
        or heartbeat.get("version") != SERVICE_VERSION
        or heartbeat.get("ready") is not True
        or heartbeat.get("vaultRoot") != str(VAULT_ROOT)
    ):
        return None
    return f"http://{host}:{port}/"


def write_ready_file(path: str | None, url: str) -> None:
    if not path:
        return
    Path(path).write_text(f"{url}\n", encoding="utf-8")


def parent_process_is_alive(parent_pid: int) -> bool:
    if parent_pid <= 0:
        return True
    if sys.platform == "win32":
        import ctypes

        synchronize = 0x00100000
        wait_timeout = 0x00000102
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, parent_pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(parent_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def codex_binary() -> Path | None:
    configured = os.environ.get("BOK_CODEX_BINARY", "").strip()
    candidates = [Path(configured).expanduser()] if configured else []
    located = shutil.which("codex")
    if located:
        candidates.append(Path(located))
    if sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
                Path("/Applications/Codex.app/Contents/Resources/codex"),
                Path.home() / ".local/bin/codex",
                Path("/opt/homebrew/bin/codex"),
                Path("/usr/local/bin/codex"),
            ]
        )
    elif sys.platform == "win32":
        local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
        if str(local_app_data):
            candidates.extend(
                [
                    local_app_data / "Programs/ChatGPT/resources/codex.exe",
                    local_app_data / "Programs/Codex/resources/codex.exe",
                ]
            )
    return next((path.resolve() for path in candidates if path.is_file()), None)


def mcp_stdio_command() -> list[str]:
    if getattr(sys, "frozen", False):
        launcher = [sys.executable]
    else:
        python = Path(sys.executable)
        if sys.platform == "win32" and python.name.lower() == "pythonw.exe":
            console_python = python.with_name("python.exe")
            if console_python.is_file():
                python = console_python
        launcher = [str(python), str(Path(__file__).resolve())]
    return launcher + [
        "--mcp-stdio",
        "--vault-root",
        str(VAULT_ROOT),
        "--bok-package-root",
        str(BOK_PACKAGE_ROOT),
    ]


def connect_codex_mcp() -> dict:
    codex = codex_binary()
    if codex is None:
        return {
            "ok": False,
            "status": "codex_not_found",
            "message": "没有找到本机 Codex。请先安装或打开 Codex，再回到这里重试。",
        }
    run_options = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "timeout": 15,
        "check": False,
    }
    if sys.platform == "win32":
        run_options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        existing = subprocess.run(
            [str(codex), "mcp", "get", "bok-desktop"],
            **run_options,
        )
        if existing.returncode == 0:
            return {
                "ok": True,
                "status": "already_connected",
                "message": "Codex 已连接 Bok。新建一个 Codex 任务后会自动加载。",
            }
        created = subprocess.run(
            [str(codex), "mcp", "add", "bok-desktop", "--", *mcp_stdio_command()],
            **run_options,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "status": "failed", "message": f"连接失败：{error}"}
    if created.returncode != 0:
        return {
            "ok": False,
            "status": "failed",
            "message": "Codex 没有接受连接配置，请完全退出 Codex 后重试。",
        }
    return {
        "ok": True,
        "status": "connected",
        "message": "连接完成。新建一个 Codex 任务后，Bok 会静默观察并按需调用记忆。",
    }


def run_mcp_stdio() -> int:
    from bok_core.cli import run as run_bok_cli

    sys.argv = [sys.argv[0], "--vault", str(VAULT_ROOT), "mcp"]
    return run_bok_cli()


def bind_server(host: str, port: int, *, allow_random_fallback: bool) -> PreviewServer:
    try:
        return PreviewServer((host, port))
    except OSError as error:
        if not allow_random_fallback or error.errno != errno.EADDRINUSE:
            raise
        print(
            f"Port {port} is occupied by another service; using a random local port.",
            flush=True,
        )
        return PreviewServer((host, 0))


def run_preview() -> None:
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/markdown; charset=utf-8", ".md")
    server_only = "--server-only" in sys.argv
    ready_file = argument_value("--ready-file")
    try:
        idle_timeout = float(argument_value("--idle-timeout") or IDLE_TIMEOUT_SECONDS)
    except ValueError as error:
        raise RuntimeError("--idle-timeout requires a numeric value.") from error
    try:
        parent_pid = int(argument_value("--parent-pid") or "0")
    except ValueError as error:
        raise RuntimeError("--parent-pid requires an integer value.") from error
    host = "127.0.0.1"

    if not APP_DIR.is_dir():
        raise RuntimeError(f"UI root is not a directory: {APP_DIR}")
    if not VAULT_ROOT.is_dir():
        raise RuntimeError(f"Vault root is not a directory: {VAULT_ROOT}")

    if server_only:
        try:
            requested_port = int(argument_value("--server-only") or "8765")
        except ValueError as error:
            raise RuntimeError("--server-only requires a numeric port.") from error
    else:
        requested_port = DEFAULT_MACOS_PORT if sys.platform == "darwin" else 0

    if not server_only and requested_port:
        existing_url = matching_preview_url(host, requested_port)
        if existing_url:
            if not open_app_window(existing_url):
                raise RuntimeError("No supported browser could open the preview window.")
            print(f"Reused existing Boujoy preview: {existing_url}", flush=True)
            write_ready_file(ready_file, existing_url)
            return

    server = bind_server(
        host,
        requested_port,
        allow_random_fallback=not server_only and bool(requested_port),
    )
    server.timeout = 1
    bound_host, bound_port = server.server_address
    url = f"http://{bound_host}:{bound_port}/"
    try:
        # Publish the live local service before the first document scan. Large
        # Vaults load through /api/vault while heartbeat and the UI stay usable.
        if not server_only and not open_app_window(url):
            raise RuntimeError("No supported browser could open the preview window.")
        print(f"Boujoy preview URL: {url}", flush=True)
        write_ready_file(ready_file, url)
        while (
            parent_process_is_alive(parent_pid)
            and (idle_timeout <= 0 or time.monotonic() - server.last_request < idle_timeout)
        ):
            server.handle_request()
    finally:
        server.server_close()
        if ready_file:
            ready_path = Path(ready_file)
            try:
                if ready_path.read_text(encoding="utf-8").strip() == url.rstrip("/") + "/":
                    ready_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def remove_launchd_job(label: str | None) -> None:
    if sys.platform != "darwin" or not label:
        return
    try:
        subprocess.run(
            ["/bin/launchctl", "remove", label],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return


def main() -> None:
    if "--mcp-stdio" in sys.argv:
        raise SystemExit(run_mcp_stdio())
    launchd_label = argument_value("--launchd-label")
    try:
        try:
            run_preview()
        except KeyboardInterrupt:
            return
    finally:
        remove_launchd_job(launchd_label)


if __name__ == "__main__":
    main()
