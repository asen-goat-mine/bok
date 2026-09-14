"""Exercise a real release package on a fresh, disposable GitHub desktop runner.

This intentionally refuses existing Bok data. It never runs on a maintainer's
normal desktop and uses only the packaged starter Vault and synthetic notes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from privacy_audit import scan_path
from release_metadata import release_version


OPENER = build_opener(ProxyHandler({}))
MARKER = "# Package acceptance\n\nSynthetic note retained after reinstall.\n"


def wait_until(check, label: str, seconds: float = 60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.2)
    raise RuntimeError(f"Timed out: {label}")


def request(base: str, route: str, body=None):
    headers = {"Origin": base, "Referer": base + "/", "Sec-Fetch-Site": "same-origin"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    data = None if body is None else json.dumps(body).encode()
    with OPENER.open(Request(base + route, data=data, headers=headers), timeout=15) as response:
        return json.load(response)


def service_stopped(base: str) -> bool:
    try:
        request(base, "/api/heartbeat")
    except (OSError, URLError):
        return True
    return False


def stop_app(process: subprocess.Popen, bundle: Path) -> None:
    try:
        if sys.platform == "win32":
            # Close this exact process's native window and require normal exit.
            command = f"$p = Get-Process -Id {process.pid}; if (-not $p.CloseMainWindow()) {{ exit 1 }}"
            subprocess.run(["pwsh", "-NoProfile", "-Command", command], check=True, timeout=20)
        else:
            script = f"tell application {json.dumps(str(bundle))} to quit"
            subprocess.run(["osascript", "-e", script], check=True, timeout=20)
        process.wait(timeout=20)
        if process.returncode != 0:
            raise RuntimeError(f"Application did not exit normally: {process.returncode}")
    finally:
        # Failure cleanup targets only the child that this test started.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def exercise(program: Path, bundle: Path, data_root: Path, version: str, *, preserved: bool) -> None:
    vault = data_root / "Vault"
    note = vault / "02-Projects/package-acceptance.md"
    personal = data_root / "Personal Core/package-acceptance.txt"
    ready = data_root / "runtime-control/preview-ready.txt"
    # A normal forced backend stop may leave a stale ready file. The new native
    # launch clears it; removing it here prevents reading the prior process URL.
    ready.unlink(missing_ok=True)
    base = None
    process = subprocess.Popen([str(program)], stdin=subprocess.DEVNULL)
    try:
        def started():
            if process.poll() is not None:
                raise RuntimeError(f"Installed app exited during startup: {process.returncode}")
            if not ready.is_file():
                return None
            candidate = ready.read_text(encoding="utf-8").strip().rstrip("/")
            parsed = urlparse(candidate)
            if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
                raise RuntimeError("Native app reported an invalid loopback URL")
            try:
                heartbeat = request(candidate, "/api/heartbeat")
            except (OSError, URLError):
                return None
            if heartbeat.get("service") != "boujoy-knowledge-preview" or not heartbeat.get("ready"):
                return None
            assert heartbeat["nativeShell"] is True
            assert Path(heartbeat["vaultRoot"]).resolve() == vault.resolve()
            return candidate

        base = wait_until(started, "installed native backend", 120)
        payload = request(base, "/api/vault")
        assert "02-Projects/welcome-to-bok.md" in {item["path"] for item in payload["files"]}
        assert not payload["unreadable"]
        health = request(base, "/api/bok/v1/health")
        assert health["version"] == version, health.get("version")
        background = request(base, "/api/bok/v1/background")
        if preserved:
            assert note.read_text(encoding="utf-8") == MARKER
            assert personal.read_text(encoding="utf-8") == MARKER
            assert background["paused"] is True
            assert background["batch_limit"] == 2
            assert background["interval_seconds"] == 60
        else:
            assert background["paused"] is False
            note.write_text(MARKER, encoding="utf-8")
            personal.write_text(MARKER, encoding="utf-8")
            updated = request(base, "/api/bok/v1/background", {
                "paused": True, "batch_limit": 2, "interval_seconds": 60,
            })
            assert updated["paused"] is True
        print("PASS: " + ("reinstall preserves Vault, Personal Core and settings" if preserved else "fresh native startup, bundled core and background controls"), flush=True)
    finally:
        if process.poll() is None:
            stop_app(process, bundle)
        elif sys.exc_info()[0] is None:
            raise RuntimeError(f"Installed app exited before the close check: {process.returncode}")
        if base:
            wait_until(lambda: service_stopped(base), "backend shutdown", 30)
    print("PASS: native close exits and loopback service stops", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="macOS ZIP or Windows NSIS installer")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform not in {"darwin", "win32"}:
        raise RuntimeError("Run only on a fresh disposable GitHub macOS/Windows runner")
    data_root = (Path(os.environ["LOCALAPPDATA"]) if sys.platform == "win32"
                 else Path.home() / "Library/Application Support") / "com.boujoy.bok"
    if data_root.exists():
        raise RuntimeError("Refusing to touch existing Bok application data")
    artifact = args.artifact.resolve(strict=True)
    expected = dict(line.split("  ", 1)[::-1] for line in
                    (artifact.parent / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines())
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == expected[artifact.name]
    version = release_version(Path(__file__).resolve().parents[2])
    with tempfile.TemporaryDirectory(prefix="bok-installed-") as temporary:
        # macOS /var is a symlink to /private/var. Tauri intentionally refuses
        # executable paths through symlinks; launch the canonical installed path.
        install = Path(temporary).resolve() / "application"
        if sys.platform == "win32":
            if " " in str(install):
                raise RuntimeError("Use a runner temp path without spaces for the NSIS /D argument")
            bundle = install
            program = install / "bok-desktop.exe"
            def unpack():
                subprocess.run([str(artifact), "/S", f"/D={install}"], check=True, timeout=300)
        else:
            bundle = install / "Bok.app"
            program = None
            def unpack():
                subprocess.run(["ditto", "-x", "-k", str(artifact), str(install)], check=True, timeout=90)
                subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], check=True, timeout=60)
        for preserved in (False, True):
            unpack()
            if sys.platform == "darwin":
                with (bundle / "Contents/Info.plist").open("rb") as stream:
                    executable = plistlib.load(stream)["CFBundleExecutable"]
                program = bundle / "Contents/MacOS" / executable
            assert program.is_file(), "Installed executable missing"
            issues = scan_path(bundle, [])
            assert not issues, "\n".join(issues)
            exercise(program, bundle, data_root, version, preserved=preserved)
        if sys.platform == "win32":
            uninstallers = list(install.glob("*ninstall*.exe"))
            assert len(uninstallers) == 1, "Expected one packaged uninstaller"
            subprocess.run([str(uninstallers[0]), "/S"], check=True, timeout=120)
            wait_until(lambda: not program.exists(), "uninstall removes application", 30)
            assert (data_root / "Vault/02-Projects/package-acceptance.md").read_text(encoding="utf-8") == MARKER
            assert (data_root / "Personal Core/package-acceptance.txt").read_text(encoding="utf-8") == MARKER
            print("PASS: uninstall removes program and retains user data", flush=True)
    print(f"PASS: installed Bok {version} on {sys.platform}; no model was started", flush=True)


if __name__ == "__main__":
    main()
