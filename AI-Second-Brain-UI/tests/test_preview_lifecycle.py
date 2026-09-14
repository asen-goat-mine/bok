"""Temporary-Vault contracts for cache reuse and responsive startup."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import ProxyHandler, build_opener


def load_preview():
    path = Path(__file__).resolve().parents[1] / "web_preview.pyw"
    loader = importlib.machinery.SourceFileLoader("preview_lifecycle_tests", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class PreviewLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.preview = load_preview()

    def test_one_changed_card_does_not_reread_other_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first, second = root / "first.md", root / "second.md"
            first.write_text("# first", encoding="utf-8")
            second.write_text("# second", encoding="utf-8")
            with patch.object(self.preview, "VAULT_ROOT", root):
                cache = self.preview.VaultCache()
                before, _ = cache.read()
                first.write_text("# first changed", encoding="utf-8")
                original_open = Path.open
                opened = []

                def track(path, *args, **kwargs):
                    opened.append(path)
                    return original_open(path, *args, **kwargs)

                with patch.object(Path, "open", track):
                    after, raw = cache.read()
                self.assertNotEqual(before, after)
                self.assertIn(first, opened)
                self.assertNotIn(second, opened)
                self.assertEqual(len(json.loads(raw)["files"]), 2)
                second.unlink()
                _, raw = cache.read()
                self.assertEqual([item["path"] for item in json.loads(raw)["files"]], ["first.md"])
                self.assertNotIn("second.md", cache.entries)

    def test_periodic_byte_validation_detects_preserved_file_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            card = root / "card.md"
            card.write_text("# before", encoding="utf-8")
            with patch.object(self.preview, "VAULT_ROOT", root):
                cache = self.preview.VaultCache()
                before, _ = cache.read()
                original_stat = Path.stat
                old_stat = card.stat()
                card.write_text("# after!", encoding="utf-8")
                def stat(path, *args, **kwargs):
                    return old_stat if path == card else original_stat(path, *args, **kwargs)
                with patch.object(Path, "stat", stat):
                    self.assertEqual(cache.read()[0], before)
                    cache.last_verified -= 31
                    after, payload = cache.read()
                self.assertNotEqual(before, after)
                self.assertEqual(json.loads(payload)["files"][0]["text"], "# after!")

    def test_bounded_http_cache_coalesces_concurrent_polls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "card.md").write_text("# card", encoding="utf-8")
            with patch.object(self.preview, "VAULT_ROOT", root):
                cache = self.preview.VaultCache()
                with patch.object(cache, "markdown_paths", wraps=cache.markdown_paths) as scan:
                    results = []
                    threads = [threading.Thread(target=lambda: results.append(cache.read(max_age=1))) for _ in range(8)]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join(timeout=5)
                    self.assertEqual(len(results), 8)
                    self.assertEqual(scan.call_count, 1)
                    self.assertTrue(all(item == results[0] for item in results))
                    cache.read()  # explicit reads still recheck the disk
                    self.assertEqual(scan.call_count, 2)

    def test_transient_read_failure_is_retried_without_rereading_healthy_cards(self):
        for warm_cache in (False, True):
            with self.subTest(warm_cache=warm_cache), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                locked, healthy = root / "locked.md", root / "healthy.md"
                locked.write_text("# Original", encoding="utf-8")
                healthy.write_text("# Healthy", encoding="utf-8")
                with patch.object(self.preview, "VAULT_ROOT", root):
                    cache = self.preview.VaultCache()
                    if warm_cache:
                        cache.read()
                        locked.write_text("# Updated", encoding="utf-8")
                    original_open = Path.open

                    def fail_locked(path, *args, **kwargs):
                        if path == locked:
                            raise PermissionError("temporary sharing violation")
                        return original_open(path, *args, **kwargs)

                    with patch.object(Path, "open", fail_locked):
                        failed_etag, failed_raw = cache.read()
                    self.assertEqual(len(json.loads(failed_raw)["unreadable"]), 1)
                    opened = []

                    def track(path, *args, **kwargs):
                        opened.append(path)
                        return original_open(path, *args, **kwargs)

                    with patch.object(Path, "open", track):
                        recovered_etag, recovered_raw = cache.read()
                    recovered = json.loads(recovered_raw)
                    self.assertNotEqual(failed_etag, recovered_etag)
                    self.assertEqual(recovered["unreadable"], [])
                    self.assertEqual({item["path"] for item in recovered["files"]}, {"locked.md", "healthy.md"})
                    self.assertEqual(opened, [locked])

    def test_same_size_content_change_with_preserved_mtime_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            card = root / "card.md"
            card.write_text("# First", encoding="utf-8")
            with patch.object(self.preview, "VAULT_ROOT", root):
                cache = self.preview.VaultCache()
                before_etag, _ = cache.read()
                before_stat = card.stat()
                card.write_text("# Other", encoding="utf-8")
                os.utime(card, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
                if card.stat().st_ctime_ns == before_stat.st_ctime_ns:
                    self.skipTest("Filesystem does not expose a changed ctime for this edit")
                after_etag, raw = cache.read()
                self.assertNotEqual(before_etag, after_etag)
                self.assertEqual(json.loads(raw)["files"][0]["text"], "# Other")

    def test_change_between_stat_and_open_is_reconciled_on_the_next_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            card = root / "card.md"
            card.write_text("# Before", encoding="utf-8")
            with patch.object(self.preview, "VAULT_ROOT", root):
                cache = self.preview.VaultCache()
                original_open = Path.open
                changed = False

                def change_before_open(path, *args, **kwargs):
                    nonlocal changed
                    if path == card and not changed:
                        changed = True
                        with original_open(card, "wb") as target:
                            target.write(b"# Changed during scan")
                    return original_open(path, *args, **kwargs)

                with patch.object(Path, "open", change_before_open):
                    cache.read()
                _, raw = cache.read()
                record = json.loads(raw)["files"][0]
                self.assertEqual(record["text"], "# Changed during scan")
                self.assertEqual(record["size"], card.stat().st_size)

    def test_runtime_and_build_copies_do_not_enter_knowledge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "card.md").write_text("# card", encoding="utf-8")
            for name in (".bok", "target", "build-resources"):
                (root / name).mkdir()
                (root / name / "duplicate.md").write_text("# duplicate", encoding="utf-8")
            with patch.object(self.preview, "VAULT_ROOT", root):
                files, _ = self.preview.VaultCache.markdown_paths()
                self.assertEqual(files, [root / "card.md"])

    def test_loopback_bind_does_not_depend_on_reverse_dns(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.preview, "VAULT_ROOT", Path(directory).resolve()), patch("socket.getfqdn", side_effect=AssertionError("Reverse DNS must not be used")):
                server = self.preview.PreviewServer(("127.0.0.1", 0))
                try:
                    self.assertEqual(server.server_name, "127.0.0.1")
                    self.assertEqual(server.server_port, server.server_address[1])
                finally:
                    server.server_close()

    def test_slow_vault_scan_does_not_block_handshake_or_heartbeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            ready = root / "ready"
            stop, scan_started, release_scan = threading.Event(), threading.Event(), threading.Event()
            cache = self.preview.VaultCache()
            original_read = cache.read

            def slow_read(**kwargs):
                scan_started.set()
                release_scan.wait(5)
                return original_read(**kwargs)

            args = ["preview", "--server-only", "0", "--ready-file", str(ready), "--idle-timeout", "0"]
            opener = build_opener(ProxyHandler({}))
            with patch.object(sys, "argv", args), patch.object(self.preview, "VAULT_ROOT", root), patch.object(self.preview, "CACHE", cache), patch.object(cache, "read", slow_read), patch.object(self.preview, "parent_process_is_alive", lambda _: not stop.is_set()):
                server = threading.Thread(target=self.preview.run_preview, daemon=True)
                server.start()
                deadline = time.monotonic() + 5
                try:
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(ready.exists(), "handshake waited for full scan")
                    self.assertFalse(scan_started.is_set())
                    url = ready.read_text().strip()
                    def request_vault():
                        with opener.open(url + "api/vault", timeout=6) as response:
                            response.read()
                    reader = threading.Thread(target=request_vault)
                    reader.start()
                    self.assertTrue(scan_started.wait(2))
                    with opener.open(url + "api/heartbeat", timeout=1) as response:
                        heartbeat = json.load(response)
                    self.assertTrue(heartbeat["ready"])
                    self.assertFalse(heartbeat["indexReady"])
                    release_scan.set()
                    reader.join(timeout=5)
                    self.assertFalse(reader.is_alive())
                finally:
                    release_scan.set()
                    stop.set()
                    server.join(timeout=3)
                self.assertFalse(server.is_alive())
                self.assertFalse(ready.exists())


if __name__ == "__main__":
    unittest.main()
