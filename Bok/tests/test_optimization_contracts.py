import tempfile
import json
import threading
import unittest
from pathlib import Path

from bok_core.config import BokConfig
from bok_core.service import BokService
from bok_core.search import VaultSearch
from bok_core.storage import VaultStorage
from bok_core.api import BokAPIServer
from bok_core.ui_bridge import BokUIBridge


class OptimizationContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = BokConfig(vault_root=self.root, provider="none", port=0)

    def test_generated_copies_never_enter_either_index(self):
        for relative in ["03-Knowledge/real.md", "Bok-Desktop/starter-vault/fake.md", "target/fake.md", "output/fake.md", ".bok/fake.md"]:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Searchable needle\n\nneedle content", encoding="utf-8")
        search = VaultSearch(self.config, VaultStorage(self.config))
        for scope in ("default", "all"):
            self.assertEqual({item["path"] for item in search.search("needle", scope=scope, semantic=False)["results"]}, {"03-Knowledge/real.md"})

    def test_unrelated_style_and_negative_style_request_do_not_force_a_route(self):
        documents = {"04-Content/cover-style.md": "# 我的封面风格\n\n像素风格。", "03-Knowledge/transformer.md": "# Transformer 原理\n\nTransformer 用注意力机制。"}
        for relative, content in documents.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        search = VaultSearch(self.config, VaultStorage(self.config))
        for query in ("按我的风格讲 Transformer 原理", "不要按我的风格，解释 Transformer 原理"):
            first = search.search(query, semantic=False)["results"][0]
            self.assertEqual(first["path"], "03-Knowledge/transformer.md")
            self.assertNotIn("style_guide_route", first["why"])

    def test_shared_api_is_authenticated_and_not_stopped_with_the_preview(self):
        service = BokService(self.config)
        service.initialize()
        server = BokAPIServer(("127.0.0.1", 0), service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            bridge = BokUIBridge(self.root, shared_api_port=server.server_address[1])
            automatic = BokUIBridge(self.root, config_overrides={"port": server.server_address[1]})
            self.assertEqual(automatic.forward("GET", "/api/bok/v1/health").status, 200)
            self.assertIsNone(automatic._server)
            automatic.close()
            response = bridge.forward("GET", "/api/bok/v1/health")
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.body)["service"], "bok-memory")
            self.assertIsNone(bridge._server)
            bridge.close()
            self.assertTrue(thread.is_alive())
            wrong_root = self.root / "wrong-vault"
            (wrong_root / ".bok").mkdir(parents=True)
            (wrong_root / ".bok/auth-token").write_text("unrelated-token")
            wrong = BokUIBridge(wrong_root, shared_api_port=server.server_address[1])
            self.assertEqual(wrong.forward("GET", "/api/bok/v1/health").status, 503)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
