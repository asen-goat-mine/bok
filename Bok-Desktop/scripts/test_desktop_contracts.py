from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from privacy_audit import scan_path


PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
RESOURCES = PROJECT / "build-resources"


class DesktopContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not RESOURCES.is_dir():
            raise RuntimeError("Run prepare_share.py before desktop contract tests")
        cls.temporary = tempfile.TemporaryDirectory(prefix="bok-desktop-contract-")
        cls.runtime = Path(cls.temporary.name)
        cls.ready = cls.runtime / "ready.txt"
        cls.control = cls.runtime / "control"
        command = [
            sys.executable,
            str(RESOURCES / "windows-source" / "web_preview.pyw"),
            "--server-only",
            "0",
            "--ready-file",
            str(cls.ready),
            "--ui-root",
            str(RESOURCES / "ui"),
            "--vault-root",
            str(RESOURCES / "starter-vault"),
            "--bok-package-root",
            str(RESOURCES / "windows-source"),
            "--native-control-dir",
            str(cls.control),
            "--idle-timeout",
            "0",
            "--parent-pid",
            str(os.getpid()),
        ]
        cls.process = subprocess.Popen(
            command,
            cwd=cls.runtime,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if cls.ready.is_file():
                cls.base_url = cls.ready.read_text(encoding="utf-8").strip().rstrip("/")
                break
            if cls.process.poll() is not None:
                output = cls.process.stdout.read() if cls.process.stdout else ""
                raise RuntimeError(f"Desktop backend exited during startup:\n{output}")
            time.sleep(0.05)
        else:
            cls.process.terminate()
            try:
                cls.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)
            output = cls.process.stdout.read() if cls.process.stdout else ""
            raise RuntimeError(
                "Desktop backend did not produce a ready file.\n"
                f"Captured output:\n{output or '<none>'}"
            )
        cls.opener = build_opener(ProxyHandler({}))

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.process.poll() is None:
            cls.process.terminate()
            try:
                cls.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)
        if cls.process.stdout:
            cls.process.stdout.close()
        cls.temporary.cleanup()

    @classmethod
    def request(cls, path: str, **kwargs):
        return cls.opener.open(Request(f"{cls.base_url}{path}", **kwargs), timeout=10)

    def test_staged_server_uses_only_starter_vault(self) -> None:
        with self.request("/api/heartbeat") as response:
            heartbeat = json.load(response)
        self.assertTrue(heartbeat["nativeShell"])
        self.assertEqual(Path(heartbeat["vaultRoot"]), RESOURCES / "starter-vault")
        with self.request("/api/vault") as response:
            payload = json.load(response)
        paths = {item["path"] for item in payload["files"]}
        self.assertIn("02-Projects/welcome-to-bok.md", paths)
        self.assertNotIn("02-Projects/boujoy-harness-product.md", paths)
        self.assertFalse(payload["unreadable"])

    def test_native_quick_note_requires_same_origin_and_writes_signal(self) -> None:
        signal = self.control / "open-quick-note.request"
        signal.unlink(missing_ok=True)
        headers = {
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
            "Sec-Fetch-Site": "same-origin",
            "Accept": "application/json",
        }
        with self.request("/api/native/quick-note", method="POST", headers=headers, data=b"") as response:
            self.assertEqual(response.status, 202)
            self.assertTrue(json.load(response)["native"])
        self.assertTrue(signal.is_file())

        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/api/native/quick-note",
                method="POST",
                headers={"Origin": "https://example.com", "Sec-Fetch-Site": "cross-site"},
                data=b"",
            )
        self.assertEqual(caught.exception.code, 403)

    def test_native_vault_picker_requires_same_origin_and_writes_signal(self) -> None:
        signal = self.control / "select-vault.request"
        signal.unlink(missing_ok=True)
        headers = {
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
            "Sec-Fetch-Site": "same-origin",
            "Accept": "application/json",
        }
        with self.request("/api/native/select-vault", method="POST", headers=headers, data=b"") as response:
            self.assertEqual(response.status, 202)
            self.assertTrue(json.load(response)["native"])
        self.assertTrue(signal.is_file())

        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/api/native/select-vault",
                method="POST",
                headers={"Origin": "https://example.com", "Sec-Fetch-Site": "cross-site"},
                data=b"",
            )
        self.assertEqual(caught.exception.code, 403)

    def test_package_contract_has_both_native_launch_paths(self) -> None:
        source = (PROJECT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
        config = json.loads((PROJECT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        windows = json.loads((PROJECT / "src-tauri" / "tauri.windows.conf.json").read_text(encoding="utf-8"))
        loading = (PROJECT / "frontend" / "loading.html").read_text(encoding="utf-8")
        self.assertIn('sidecar("bok-preview")', source)
        self.assertIn('windows-python/pythonw.exe', source)
        self.assertIn('CREATE_NO_WINDOW', source)
        self.assertIn('PYTHONDONTWRITEBYTECODE', source)
        self.assertIn('"--parent-pid".into()', source)
        self.assertIn('app_local_data_dir()', source)
        self.assertIn('data_root.join("Personal Core")', source)
        self.assertIn('selected-vault.json', source)
        self.assertIn('blocking_pick_folder()', source)
        self.assertIn('app.request_restart()', source)
        self.assertIn('WebviewUrl::App("quick-note.html".into())', source)
        handler = re.search(r"generate_handler!\s*\[([^\]]+)\]", source)
        self.assertIsNotNone(handler)
        registered = set(re.findall(r"\b[a-z_]\w*\b", handler.group(1)))
        self.assertTrue({"quick_note_status", "quick_note_save", "retry_startup", "startup_status"}.issubset(registered))
        self.assertIn('require_quick_note_window(&window)?', source)
        self.assertIn('POST /api/bok/v1/quick-notes HTTP/1.1', source)
        self.assertIn('if !default_vault.exists()', source)
        self.assertIn('replace_file(&temporary, &config_path)', source)
        self.assertEqual(config["identifier"], "com.boujoy.bok")
        self.assertEqual(config["app"]["windows"][0]["theme"], "Dark")
        self.assertEqual(config["app"]["windows"][0]["titleBarStyle"], "Transparent")
        self.assertEqual(config["app"]["windows"][0]["backgroundColor"], [8, 11, 16, 255])
        self.assertTrue(config["app"]["withGlobalTauri"])
        self.assertEqual([window["label"] for window in config["app"]["windows"]], ["main"])
        self.assertIn("dmg", config["bundle"]["targets"])
        self.assertFalse(config["bundle"]["macOS"]["hardenedRuntime"])
        self.assertEqual(windows["bundle"]["windows"]["nsis"]["installMode"], "currentUser")
        self.assertTrue(windows["bundle"]["windows"]["webviewInstallMode"]["silent"])
        self.assertIn('src="bok-k-icon.png"', loading)
        self.assertIn('animation: loading .56s', loading)
        self.assertTrue((PROJECT / "frontend" / "bok-k-icon.png").is_file())
        self.assertTrue((PROJECT / "frontend" / "quick-note.html").is_file())
        self.assertTrue((PROJECT / "frontend" / "quick-note-window.js").is_file())
        self.assertTrue((PROJECT / "frontend" / "quick-note-window.css").is_file())
        self.assertTrue((PROJECT / "frontend" / "assets" / "boujoy-knowledge-pop-collage-v2.png").is_file())
        self.assertTrue((PROJECT / "frontend" / "assets" / "fusion-pixel-10px-proportional-zh_hans.otf.woff2").is_file())
        self.assertTrue((PROJECT / "src-tauri" / "icons" / "icon.icns").is_file())
        self.assertTrue((PROJECT / "src-tauri" / "icons" / "icon.ico").is_file())

    def test_backend_exits_and_removes_ready_file_when_parent_is_gone(self) -> None:
        ready = self.runtime / "orphan-ready.txt"
        command = [
            sys.executable,
            str(RESOURCES / "windows-source" / "web_preview.pyw"),
            "--server-only",
            "0",
            "--ready-file",
            str(ready),
            "--ui-root",
            str(RESOURCES / "ui"),
            "--vault-root",
            str(RESOURCES / "starter-vault"),
            "--bok-package-root",
            str(RESOURCES / "windows-source"),
            "--native-control-dir",
            str(self.control),
            "--idle-timeout",
            "0",
            "--parent-pid",
            "2000000000",
        ]
        completed = subprocess.run(
            command,
            cwd=self.runtime,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=75,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertFalse(ready.exists(), completed.stdout)

    def test_resources_are_whitelisted_and_private_data_free(self) -> None:
        manifest = json.loads((RESOURCES / "share-manifest.json").read_text(encoding="utf-8"))
        paths = {item["path"] for item in manifest["files"]}
        self.assertIn("ui/index.html", paths)
        self.assertIn("starter-vault/AGENTS.md", paths)
        self.assertIn("windows-source/bok_core/api.py", paths)
        self.assertFalse(any(path.startswith("starter-vault/.bok/") for path in paths))
        if any(path.startswith("windows-python/") for path in paths):
            self.assertIn("windows-python/pythonw.exe", paths)
            self.assertIn("windows-python/python313.dll", paths)
            self.assertIn("windows-python/python313._pth", paths)
        issues = scan_path(
            RESOURCES,
            [token.encode("utf-8") for token in ("local-user", "example-account", "example-id")],
        )
        self.assertEqual(issues, [])

    def test_privacy_auditor_does_not_flag_its_own_detectors(self) -> None:
        issues = scan_path(PROJECT / "scripts" / "privacy_audit.py", [])
        self.assertEqual(issues, [])

    def test_privacy_auditor_ignores_generated_directories_only(self) -> None:
        root = self.runtime / "privacy-fixture"
        generated = root / "__pycache__"
        generated.mkdir(parents=True)
        payload = b"temporary bytecode path: /" + b"Users/" + b"example-name/private.txt"
        (generated / "module.pyc").write_bytes(payload)
        self.assertEqual(scan_path(root, []), [])

        tracked = root / "module.py"
        tracked.write_bytes(payload)
        issues = scan_path(root, [])
        self.assertTrue(any("mac_user_path" in issue for issue in issues), issues)


if __name__ == "__main__":
    unittest.main(verbosity=2)
