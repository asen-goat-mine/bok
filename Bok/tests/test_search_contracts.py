from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bok_core.config import BokConfig
from bok_core.search import VaultSearch
from bok_core.storage import VaultStorage


class SearchRoutingContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.vault = Path(self.temporary.name)
        self.focus = "02-Projects/current.md"
        self.write("00-System/Active-Context.md", f"---\nfocus_path: {self.focus}\n---\n# Active Context\n")
        self.write(self.focus, """# 当前制作项目

## 一句话结论
最初的版本已经交付。

## 当前状态
旧版等待查看。

## 下一步行动
根据用户最新反馈调整人物动作。

## 2026-09-08 更新
第一次预览完成。

## 2026-09-10 新版交付
新版完整动作已完成，等待具体反馈。
""")
        # A perfect phrase match in an unrelated document must not steal a
        # subject-free continuation request from the explicitly chosen focus.
        self.write("04-Content/noise.md", "# 接着上次做\n\n接着上次做，继续上次的项目，这是演示口播。\n")
        self.write("03-Knowledge/transformer.md", "# Transformer 架构分析\n\n继续 Transformer 架构分析，讲解注意力机制。\n")
        config = BokConfig(vault_root=self.vault, provider="none", embedding_provider="none", personal_core_root="")
        self.search = VaultSearch(config, VaultStorage(config))

    def write(self, relative: str, text: str) -> None:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def fake_local_embeddings(self) -> list:
        self.search.config.embedding_provider = "ollama"
        self.search.config.embedding_model = "offline-test"
        self.search.provider.embedding_is_local = lambda: True
        embedded = []

        def embed(texts, **_kwargs):
            embedded.extend(texts)
            return [[1.0, 0.0] for _text in texts]

        self.search.provider.embed = embed
        return embedded

    def test_generic_continuation_prefers_next_action_and_latest_update(self) -> None:
        for query in ("接着上次做", "继续", "请继续上次的项目吧", "下一步行动", "这个项目做到哪了", "当前项目进度如何"):
            with self.subTest(query=query):
                result = self.search.search(query, semantic=False, limit=6)
                self.assertEqual({item["path"] for item in result["results"]}, {self.focus})
                headings = [item["heading"] for item in result["results"]]
                expected_first = "2026-09-10 新版交付" if "做到哪" in query or "进度" in query else "下一步行动"
                self.assertEqual(headings[0], expected_first)
                self.assertLess(headings.index("2026-09-10 新版交付"), headings.index("一句话结论"))
                self.assertIn("current_project_route", result["results"][0]["why"])

    def test_continuation_follows_a_changed_focus_without_fixture_updates(self) -> None:
        self.search.search("接着上次做", semantic=False)
        new_focus = "02-Projects/another.md"
        self.write(new_focus, "# 新项目\n\n## 下一步行动\n测试新项目。\n")
        self.write("00-System/Active-Context.md", f"---\nfocus_path: {new_focus}\n---\n# Active Context\n")
        result = self.search.search("接着上次做", semantic=False)
        self.assertEqual(result["results"][0]["path"], new_focus)
        self.assertEqual(result["results"][0]["heading"], "下一步行动")

    def test_named_continuation_does_not_force_the_unrelated_focus(self) -> None:
        result = self.search.search("继续 Transformer 架构分析", semantic=False)
        self.assertEqual(result["results"][0]["path"], "03-Knowledge/transformer.md")
        self.assertNotIn("current_project_route", result["results"][0]["why"])

    def test_generic_route_respects_explicit_scope_and_tag_filters(self) -> None:
        self.write("03-Knowledge/allowed/note.md", "---\ntags: [allowed]\n---\n# 继续上次\n\n这里记录另一段项目进度。\n")
        for filters in ({"path_prefix": "03-Knowledge/allowed"}, {"tags": ["allowed"]}):
            with self.subTest(filters=filters):
                result = self.search.search("接着上次做", semantic=False, **filters)
                self.assertEqual({item["path"] for item in result["results"]}, {"03-Knowledge/allowed/note.md"})
                self.assertNotIn("current_project_route", result["results"][0]["why"])

    def test_local_semantic_candidates_preserve_path_and_tag_filters(self) -> None:
        self.write("03-Knowledge/allowed/a.md", "---\ntags: [allowed]\n---\n# Allowed card\n\n无关键词重合的材料。\n")
        self.write("03-Knowledge/allowed/wrong-tag.md", "---\ntags: [outside]\n---\n# Wrong tag\n\nOUTSIDE_TAG_MARKER\n")
        self.write("03-Knowledge/outside/b.md", "---\ntags: [outside]\n---\n# Outside card\n\nOUTSIDE_PATH_MARKER\n")
        embedded = self.fake_local_embeddings()
        for filters, expected_paths in (
            ({"path_prefix": "03-Knowledge/allowed", "tags": ["allowed"]}, {"03-Knowledge/allowed/a.md"}),
            ({"tags": ["allowed"]}, {"03-Knowledge/allowed/a.md"}),
            ({"path_prefix": "03-Knowledge/allowed"}, {"03-Knowledge/allowed/a.md", "03-Knowledge/allowed/wrong-tag.md"}),
            ({"path_prefix": "03-Knowledge/missing"}, set()),
            ({"tags": ["missing"]}, set()),
        ):
            with self.subTest(filters=filters):
                result = self.search.search("semantic needle", semantic=True, limit=20, **filters)
                self.assertEqual({item["path"] for item in result["results"]}, expected_paths)
                if expected_paths:
                    self.assertEqual(result["semantic"]["mode"], "full_local_retrieval")
                else:
                    self.assertEqual(result["semantic"]["status"], "no_candidates")
        self.assertFalse(any("OUTSIDE_PATH_MARKER" in text for text in embedded))

    def test_semantic_reranking_cannot_reintroduce_other_projects_on_resume(self) -> None:
        embedded = self.fake_local_embeddings()
        result = self.search.search("接着上次做", semantic=True, limit=6)
        self.assertEqual({item["path"] for item in result["results"]}, {self.focus})
        self.assertEqual(result["results"][0]["heading"], "下一步行动")
        self.assertFalse(any("这是演示口播" in text for text in embedded))


if __name__ == "__main__":
    unittest.main()
