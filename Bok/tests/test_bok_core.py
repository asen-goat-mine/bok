from __future__ import annotations

import json
import multiprocessing
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from bok_core.api import BokAPIServer
from bok_core.config import BokConfig
from bok_core.errors import BokError, ConflictError, NotFoundError, PermissionDeniedError
from bok_core.mcp import MCPServer
from bok_core.provider import CredentialStore, MemoryIntelligence, NetworkPolicy
from bok_core.service import BokService
from bok_core.ui_bridge import BokUIBridge
from bok_core.util import atomic_write_bytes as real_atomic_write_bytes, sha256_text


def concurrent_storage_write_worker(vault: str, expected_hash: str, text: str, ready, start, results) -> None:
    config = BokConfig(vault_root=Path(vault), provider="none", port=0, max_context_tokens=2500)
    service = BokService(config)
    service.initialize()
    ready.put(text)
    start.wait(10)
    try:
        result = service.storage.write("03-Knowledge/process-conflict.md", text, expected_hash=expected_hash)
        results.put(("ok", text, result.content_hash))
    except BokError as error:
        results.put((error.code, text, ""))


def ordinary_analysis(**overrides):
    value = {
        "summary": "Bok 应该使用段落级检索并控制上下文预算。",
        "title": "Bok 段落级检索",
        "memory_type": "knowledge",
        "confidence": 0.94,
        "sensitivity": "none",
        "importance": "ordinary",
        "action": "create",
        "target_path": "03-Knowledge/bok-paragraph-search.md",
        "tags": ["Bok", "检索"],
        "reason": "可以长期复用",
        "expires_at": "",
        "source_excerpt": "段落级检索",
    }
    value.update(overrides)
    return value


class BokCoreContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary.name).resolve()
        for name in ("00-System", "01-Inbox", "02-Projects", "03-Knowledge", "04-Content", "05-Prompts", "06-Business", "90-Archive"):
            (self.vault / name).mkdir()
        (self.vault / "00-System/Active-Context.md").write_text(
            "---\nfocus_path: 02-Projects/bok.md\n---\n\n# Active Context\n",
            encoding="utf-8",
        )
        (self.vault / "02-Projects/bok.md").write_text(
            "---\ntitle: Bok\ntags: [Bok, Second Brain]\n---\n\n# Bok\n\n## 当前状态\n\n本地内核开发中。\n\n## 关键决策\n\nMarkdown 是唯一事实源。\n\n## 下一步行动\n\n- 完成 Memory API。\n",
            encoding="utf-8",
        )
        (self.vault / "03-Knowledge/retrieval.md").write_text(
            "---\ntitle: 混合检索\naliases: [语义搜索]\ntags: [检索]\n---\n\n# 混合检索\n\n## 段落召回\n\nBok 只返回与当前任务最相关的段落，避免把整个知识库发送给模型。\n\n## Token 预算\n\n默认上下文预算为二千五百 Token。\n",
            encoding="utf-8",
        )
        self.config = BokConfig(vault_root=self.vault, provider="none", port=0, max_context_tokens=2500)
        self.service = BokService(self.config)
        self.service.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_non_loopback_bind_is_rejected(self) -> None:
        with self.assertRaises(BokError) as caught:
            BokConfig(vault_root=self.vault, host="0.0.0.0")
        self.assertEqual(caught.exception.code, "unsafe_bind")

    def test_write_path_traversal_is_rejected(self) -> None:
        with self.assertRaises(PermissionDeniedError):
            self.service.storage.write("03-Knowledge/../../outside.md", "bad")

    def test_write_outside_allowed_roots_is_rejected(self) -> None:
        with self.assertRaises(PermissionDeniedError):
            self.service.storage.write("00-System/owned.md", "bad")

    def test_document_reader_cannot_expose_runtime_secrets(self) -> None:
        self.assertTrue((self.vault / ".bok/auth-token").is_file())
        with self.assertRaises(PermissionDeniedError):
            self.service.read_document(".bok/auth-token")
        (self.vault / ".bok/private.md").write_text("secret", encoding="utf-8")
        with self.assertRaises(PermissionDeniedError):
            self.service.read_document(".bok/private.md")

    def test_symlink_write_is_rejected(self) -> None:
        outside = self.vault.parent / f"{self.vault.name}-outside"
        outside.mkdir(exist_ok=True)
        link = self.vault / "03-Knowledge/link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Symlink unavailable: {error}")
        try:
            with self.assertRaises(PermissionDeniedError):
                self.service.storage.write("03-Knowledge/link/escape.md", "bad")
        finally:
            link.unlink(missing_ok=True)
            outside.rmdir()

    def test_atomic_write_version_and_rollback(self) -> None:
        first = self.service.storage.write("03-Knowledge/new.md", "# First\n")
        second = self.service.storage.write("03-Knowledge/new.md", "# Second\n", expected_hash=first.content_hash)
        self.assertEqual(self.service.storage.read_text("03-Knowledge/new.md"), "# Second\n")
        rolled = self.service.storage.rollback(second.version_id)
        self.assertEqual(rolled.operation, "rollback")
        self.assertEqual(self.service.storage.read_text("03-Knowledge/new.md"), "# First\n")

    def test_concurrent_change_is_not_overwritten(self) -> None:
        first = self.service.storage.write("03-Knowledge/conflict.md", "one")
        self.service.storage.write("03-Knowledge/conflict.md", "two", expected_hash=first.content_hash)
        with self.assertRaises(ConflictError):
            self.service.storage.write("03-Knowledge/conflict.md", "stale", expected_hash=first.content_hash)

    def test_concurrent_document_creates_only_allow_one_writer(self) -> None:
        path = "03-Knowledge/concurrent-create.md"
        barrier = threading.Barrier(2)
        real_content_hash = self.service.storage.content_hash

        def read_before_either_writer_creates(relative):
            value = real_content_hash(relative)
            barrier.wait(timeout=5)
            return value

        def create(text):
            try:
                self.service.write_document(path, text, expected_hash=None)
                return "ok", text
            except BokError as error:
                return error.code, text

        with patch.object(self.service.storage, "content_hash", side_effect=read_before_either_writer_creates):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(create, ("writer-a", "writer-b")))
        self.assertEqual(sorted(item[0] for item in outcomes), ["conflict", "ok"])
        winner = next(text for status, text in outcomes if status == "ok")
        self.assertEqual(self.service.storage.read_text(path), winner)
        self.assertEqual(len(self.service.storage.list_versions(path)), 1)


    def test_document_rollback_refuses_corrupt_or_missing_snapshot(self) -> None:
        for condition in ("corrupt", "missing"):
            with self.subTest(condition=condition):
                path = f"03-Knowledge/rollback-{condition}.md"
                original = self.service.write_document(path, "# Original\n", expected_hash=None)
                changed = self.service.write_document(path, "# Newest\n", expected_hash=original["content_hash"])
                snapshot = self.service.storage.versions / changed["version_id"] / "before.md"
                if condition == "corrupt":
                    snapshot.write_text("# Damaged snapshot\n", encoding="utf-8")
                else:
                    snapshot.unlink()
                with self.assertRaises(BokError) as caught:
                    self.service.rollback_document(changed["version_id"])
                self.assertEqual(caught.exception.code, "version_corrupt")
                self.assertEqual(self.service.storage.read_text(path), "# Newest\n")
                self.assertEqual(len(self.service.storage.list_versions(path)), 2)


    def test_concurrent_moves_do_not_overwrite_the_same_destination(self) -> None:
        destination = "03-Knowledge/shared-destination.md"
        sources = [f"03-Knowledge/move-{index}.md" for index in range(2)]
        created = [self.service.storage.write(path, f"source-{index}") for index, path in enumerate(sources)]
        barrier = threading.Barrier(2)
        entered = threading.local()
        real_acquire = self.service.storage.lock.acquire

        def synchronize_first_lock():
            if not getattr(entered, "seen", False):
                entered.seen = True
                barrier.wait(timeout=5)
            return real_acquire()

        def move(index):
            try:
                self.service.move_document(sources[index], destination, expected_hash=created[index].content_hash)
                return "ok", index
            except BokError as error:
                return error.code, index

        with patch.object(self.service.storage.lock, "acquire", side_effect=synchronize_first_lock):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(move, range(2)))
        self.assertEqual(sorted(item[0] for item in outcomes), ["conflict", "ok"])
        winner = next(index for status, index in outcomes if status == "ok")
        loser = 1 - winner
        self.assertEqual(self.service.storage.read_text(destination), f"source-{winner}")
        self.assertFalse((self.vault / sources[winner]).exists())
        self.assertEqual(self.service.storage.read_text(sources[loser]), f"source-{loser}")
        self.assertEqual(len(self.service.storage.list_versions(destination)), 1)


    def test_cross_process_change_is_not_silently_overwritten(self) -> None:
        first = self.service.storage.write("03-Knowledge/process-conflict.md", "base")
        context = multiprocessing.get_context("spawn")
        ready = context.Queue()
        start = context.Event()
        results = context.Queue()
        processes = [
            context.Process(
                target=concurrent_storage_write_worker,
                args=(str(self.vault), first.content_hash, text, ready, start, results),
            )
            for text in ("writer-a", "writer-b")
        ]
        for process in processes:
            process.start()
        self.assertEqual({ready.get(timeout=15), ready.get(timeout=15)}, {"writer-a", "writer-b"})
        start.set()
        for process in processes:
            process.join(20)
            self.assertEqual(process.exitcode, 0)
        outcomes = [results.get(timeout=5), results.get(timeout=5)]
        self.assertEqual(sum(1 for item in outcomes if item[0] == "ok"), 1)
        self.assertEqual(sum(1 for item in outcomes if item[0] == "conflict"), 1)
        winner = next(item[1] for item in outcomes if item[0] == "ok")
        self.assertEqual(self.service.storage.read_text("03-Knowledge/process-conflict.md"), winner)

    def test_failed_document_write_does_not_publish_a_ghost_version(self) -> None:
        target = self.service.storage.resolve("03-Knowledge/write-failure.md", write=True)

        def fail_target(path, data, *, mode=0o600):
            if Path(path) == target:
                raise OSError("simulated target failure")
            return real_atomic_write_bytes(Path(path), data, mode=mode)

        with patch("bok_core.storage.atomic_write_bytes", side_effect=fail_target):
            with self.assertRaises(BokError) as caught:
                self.service.storage.write("03-Knowledge/write-failure.md", "never committed")
        self.assertEqual(caught.exception.code, "write_failed")
        self.assertFalse(target.exists())
        self.assertEqual(self.service.storage.list_versions("03-Knowledge/write-failure.md"), [])
        raw = [json.loads(path.read_text(encoding="utf-8")) for path in self.service.storage.versions.glob("*/meta.json")]
        failed = [item for item in raw if item.get("path") == "03-Knowledge/write-failure.md"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["status"], "aborted")

    def test_existing_document_requires_expected_hash(self) -> None:
        with self.assertRaises(BokError) as caught:
            self.service.write_document("03-Knowledge/retrieval.md", "overwrite", expected_hash=None)
        self.assertEqual(caught.exception.code, "precondition_required")

    def test_important_document_requires_confirmation(self) -> None:
        original = "---\ntitle: 决策\nmemory_type: decision\nimportance: important\n---\n\n# 决策\n"
        write = self.service.storage.write("02-Projects/decision.md", original)
        with self.assertRaises(BokError) as caught:
            self.service.write_document("02-Projects/decision.md", original + "changed", expected_hash=write.content_hash)
        self.assertEqual(caught.exception.code, "important_confirmation_required")
        changed = self.service.write_document("02-Projects/decision.md", original + "changed", expected_hash=write.content_hash, confirm_important=True)
        self.assertTrue(changed["version_id"])

    def test_new_important_document_cannot_hide_its_type_from_policy(self) -> None:
        important = "---\nmemory_type: decision\nimportance: important\n---\n\n# Important\n"
        with self.assertRaises(BokError) as caught:
            self.service.write_document("02-Projects/hidden-important.md", important, expected_hash=None, important=False)
        self.assertEqual(caught.exception.code, "important_confirmation_required")
        created = self.service.write_document("02-Projects/hidden-important.md", important, expected_hash=None, important=False, confirm_important=True)
        self.assertTrue(created["created"])

    def test_important_document_rollback_requires_confirmation(self) -> None:
        original = "---\ntitle: 决策\nmemory_type: decision\nimportance: important\n---\n\n# 决策\n"
        created = self.service.storage.write("02-Projects/rollback-decision.md", original)
        changed = self.service.write_document("02-Projects/rollback-decision.md", original + "changed\n", expected_hash=created.content_hash, confirm_important=True)
        with self.assertRaises(BokError) as caught:
            self.service.rollback_document(changed["version_id"])
        self.assertEqual(caught.exception.code, "important_confirmation_required")
        rolled = self.service.rollback_document(changed["version_id"], confirm_important=True)
        self.assertEqual(rolled["operation"], "rollback")

    def test_quick_note_is_one_local_markdown_file(self) -> None:
        result = self.service.create_quick_note("记得完成 Bok 安全写入。")
        self.assertTrue(result["path"].startswith("07-Quick-Notes/"))
        text = (self.vault / result["path"]).read_text(encoding="utf-8")
        self.assertIn("type: quick-note", text)
        self.assertIn("status: inbox", text)

    def test_quick_note_promotion_is_queued_without_a_modal_confirmation(self) -> None:
        note = self.service.create_quick_note("这是一条可以沉淀的知识。")
        captured = self.service.promote_quick_note(note["path"])
        self.assertEqual(captured["status"], "queued")
        self.assertEqual(self.service.list_quick_notes()["items"][0]["status"], "inbox")

    def test_quick_note_becomes_promoted_after_auto_commit(self) -> None:
        note = self.service.create_quick_note("普通知识可以后台沉淀。")
        self.service.memory.intelligence.analyze = lambda *args, **kwargs: ordinary_analysis(target_path="03-Knowledge/from-quick-note.md")
        captured = self.service.promote_quick_note(note["path"])
        self.service.process_captures(limit=1)
        self.assertEqual(self.service.capture_status(captured["id"])["status"], "completed")
        listed = self.service.list_quick_notes()["items"][0]
        self.assertEqual(listed["status"], "promoted")
        self.assertIn("from-quick-note.md", self.service.read_document(note["path"])["frontmatter"]["promoted_to"])

    def test_document_move_is_versioned_and_source_is_recoverable(self) -> None:
        created = self.service.storage.write("03-Knowledge/move-me.md", "# Move\n")
        moved = self.service.move_document("03-Knowledge/move-me.md", "03-Knowledge/moved.md", expected_hash=created.content_hash)
        self.assertFalse((self.vault / "03-Knowledge/move-me.md").exists())
        self.assertTrue((self.vault / "03-Knowledge/moved.md").is_file())
        self.service.storage.rollback(moved["source_version_id"])
        self.assertTrue((self.vault / "03-Knowledge/move-me.md").is_file())

    def test_web_clip_and_markdown_import_are_local_additive_writes(self) -> None:
        clip = self.service.create_web_clip(title="Bok Article", url="https://example.com/bok", content="Useful local-first material.", tags=["Bok"])
        self.assertTrue(clip["path"].startswith("01-Inbox/Web-Clips/"))
        self.assertIn("source_url", self.service.storage.read_text(clip["path"]))
        imported = self.service.import_markdown(text="# Imported\n", destination="01-Inbox/imported.md")
        self.assertEqual(imported["path"], "01-Inbox/imported.md")
        with self.assertRaises(BokError) as caught:
            self.service.import_markdown(text="# Overwrite\n", destination="01-Inbox/imported.md")
        self.assertEqual(caught.exception.code, "import_destination_exists")

    def test_paragraph_search_has_source_and_budget(self) -> None:
        result = self.service.search("段落 检索 模型", token_budget=180)
        self.assertTrue(result["results"])
        self.assertEqual(result["results"][0]["path"], "03-Knowledge/retrieval.md")
        self.assertIn("source_id", result["results"][0])
        self.assertLessEqual(result["token_estimate"], 180)

    def test_schema_on_read_derives_type_and_updated_without_rewriting_markdown(self) -> None:
        source = self.vault / "03-Knowledge/retrieval.md"
        before = source.read_text(encoding="utf-8")
        document = self.service.read_document("03-Knowledge/retrieval.md")
        result = self.service.search("混合检索", semantic=False)
        self.assertEqual(document["metadata"]["type"], "knowledge")
        self.assertEqual(document["metadata"]["role"], "knowledge-card")
        self.assertEqual(document["metadata"]["status"], "active")
        self.assertEqual(document["metadata"]["source"], "unspecified")
        self.assertEqual(document["metadata"]["updated_source"], "filesystem")
        self.assertTrue(document["metadata"]["updated"])
        self.assertEqual(result["results"][0]["type"], "knowledge")
        self.assertEqual(result["results"][0]["role"], "knowledge-card")
        self.assertEqual(result["results"][0]["status"], "active")
        self.assertEqual(result["results"][0]["source"], "unspecified")
        self.assertTrue(result["results"][0]["updated"])
        self.assertEqual(source.read_text(encoding="utf-8"), before)

        legacy = self.vault / "03-Knowledge/legacy-sections.md"
        legacy.write_text(
            "# 旧知识卡\n\n## 相关标签\n\n检索、排序\n\n## 来源类型\n\n历史整理\n\n## 更新时间\n\n2026-08-20\n",
            encoding="utf-8",
        )
        legacy_before = legacy.read_text(encoding="utf-8")
        legacy_document = self.service.read_document("03-Knowledge/legacy-sections.md")
        self.assertEqual(legacy_document["metadata"]["role"], "knowledge-card")
        self.assertEqual(legacy_document["metadata"]["source"], "历史整理")
        self.assertEqual(legacy_document["metadata"]["updated"], "2026-08-20")
        self.assertEqual(legacy_document["metadata"]["updated_source"], "section")
        self.assertEqual(legacy_document["metadata"]["tags"], ["检索", "排序"])
        self.assertEqual(legacy.read_text(encoding="utf-8"), legacy_before)

    def test_search_prefers_source_diversity_before_same_document_overflow(self) -> None:
        result = self.service.search("Bok 本地 知识", limit=3, semantic=False)
        paths = [item["path"] for item in result["results"]]
        if len(set(paths)) > 1:
            self.assertLessEqual(max(paths.count(path) for path in set(paths)), 2)

    def test_context_has_stable_citations(self) -> None:
        result = self.service.context("Bok Token 预算")
        self.assertTrue(result["sources"])
        self.assertEqual(result["sources"][0]["citation"], "S1")
        self.assertIn("[S1]", result["context"])

    def test_optional_embeddings_rerank_without_replacing_markdown(self) -> None:
        self.service.config.embedding_provider = "ollama"
        self.service.config.embedding_model = "test-embedding"

        def fake_embed(texts, **_kwargs):
            return [[1.0, 0.0] if "Token" in text or "预算" in text else [0.0, 1.0] for text in texts]

        self.service.search_engine.provider.embed = fake_embed
        result = self.service.search("Token 预算", semantic=True)
        self.assertEqual(result["semantic"]["status"], "applied")
        self.assertEqual(result["semantic"]["mode"], "full_local_retrieval")
        self.assertEqual(result["retrieval"], "paragraph-hybrid")
        self.assertTrue((self.vault / result["results"][0]["path"]).is_file())

    def test_local_semantic_retrieval_can_find_a_zero_keyword_match(self) -> None:
        target = self.vault / "03-Knowledge/semantic-only.md"
        target.write_text("# 无词面重合\n\n这份材料只写完全不同的表面词语。\n", encoding="utf-8")
        self.service.search_engine.invalidate()
        self.service.config.embedding_provider = "ollama"
        self.service.config.embedding_model = "fake-embedding"

        def fake_embed(texts, **_kwargs):
            return [[1.0, 0.0] if text == "semantic needle" or "无词面重合" in text else [0.0, 1.0] for text in texts]

        self.service.search_engine.provider.embed = fake_embed
        result = self.service.search("semantic needle", semantic=True)
        self.assertEqual(result["semantic"]["mode"], "full_local_retrieval")
        self.assertEqual(result["results"][0]["path"], "03-Knowledge/semantic-only.md")
        self.assertIn("semantic_match", result["results"][0]["why"])

    def test_deferred_documents_are_preserved_in_all_scope(self) -> None:
        directory = self.vault / "02-Projects/model-comparison-benchmark"
        directory.mkdir()
        target = directory / "noise.md"
        target.write_text("# Benchmark\n\nDEFERRED-UNIQUE-PHRASE\n", encoding="utf-8")
        self.service.search_engine.invalidate()
        default = self.service.search("DEFERRED UNIQUE PHRASE", semantic=False)
        expanded = self.service.search("DEFERRED UNIQUE PHRASE", semantic=False, scope="all")
        explicit = self.service.search("DEFERRED UNIQUE PHRASE", semantic=False, path_prefix="02-Projects/model-comparison-benchmark")
        self.assertFalse(any(item["path"] == "02-Projects/model-comparison-benchmark/noise.md" for item in default["results"]))
        self.assertTrue(any(item["path"] == "02-Projects/model-comparison-benchmark/noise.md" for item in expanded["results"]))
        self.assertTrue(any(item["path"] == "02-Projects/model-comparison-benchmark/noise.md" for item in explicit["results"]))
        self.assertEqual(default["scope"], "default")
        self.assertEqual(expanded["scope"], "all")

    def test_project_resume_reads_status_decisions_and_next(self) -> None:
        result = self.service.project_resume()
        self.assertEqual(result["path"], "02-Projects/bok.md")
        self.assertIn("本地内核", result["status"])
        self.assertIn("Markdown", result["decisions"])
        self.assertIn("Memory API", result["next_actions"])

    def test_focus_path_reuses_the_validated_index(self) -> None:
        with patch.object(self.service.storage, "read_text", side_effect=OSError("path temporarily unavailable")):
            self.assertEqual(self.service.search_engine.focus_path(), "02-Projects/bok.md")

    def test_ordinary_high_confidence_memory_auto_commits(self) -> None:
        self.service.memory.intelligence.analyze = lambda *args, **kwargs: ordinary_analysis()
        result = self.service.propose_memory("使用段落级检索可以减少 Token。", source={"type": "test", "ref": "turn-1"})
        self.assertEqual(result["status"], "auto_committed")
        self.assertTrue((self.vault / result["target_path"]).is_file())
        self.assertFalse(result["requires_review"])

    def test_conversation_preference_is_owned_by_personal_core_without_review_card(self) -> None:
        analysis = ordinary_analysis(
            memory_type="preference",
            importance="important",
            target_path="03-Knowledge/user-style.md",
        )
        result = self.service.memory.propose(
            "用户偏好简洁但完整的表达。",
            source={"type": "conversation", "ref": "chat:turn-1"},
            _analysis=analysis,
        )
        self.assertEqual(result["status"], "personal_core_only")
        self.assertEqual(result["memory_type"], "preference")
        self.assertEqual(self.service.memory.list(status="pending"), [])
        self.assertNotIn("analysis", result)

    def test_explicit_non_conversation_preference_remains_reviewable(self) -> None:
        analysis = ordinary_analysis(
            memory_type="preference",
            importance="important",
            target_path="03-Knowledge/explicit-style-note.md",
        )
        result = self.service.memory.propose(
            "把这条明确整理的写作偏好保存下来。",
            source={"type": "quick-note", "ref": "07-Quick-Notes/style.md"},
            _analysis=analysis,
        )
        self.assertEqual(result["status"], "pending")
        self.assertTrue(result["requires_review"])

    def test_initialize_migrates_old_conversation_profile_proposals_out_of_inbox(self) -> None:
        self.service.memory._save(
            [
                {
                    "id": "old-preference",
                    "created_at": "2026-08-23T00:00:00Z",
                    "status": "pending",
                    "material_hash": "a" * 64,
                    "source": {"type": "conversation", "ref": "old:turn"},
                    "analysis": ordinary_analysis(memory_type="identity", importance="important"),
                    "target_path": "03-Knowledge/old-profile.md",
                    "requires_review": True,
                    "review_reasons": ["important_memory"],
                }
            ]
        )
        initialized = self.service.initialize()
        self.assertEqual(initialized["personal_profile_reconcile"]["migrated"], 1)
        self.assertEqual(self.service.memory.list(status="pending"), [])
        migrated = self.service.memory.list(status="personal_core_only")
        self.assertEqual(len(migrated), 1)
        self.assertFalse(migrated[0]["requires_review"])

    def test_important_memory_is_non_blocking_pending(self) -> None:
        self.service.memory.intelligence.analyze = lambda *args, **kwargs: ordinary_analysis(
            summary="Bok 正式产品名已经确定。",
            title="Bok 产品命名决策",
            memory_type="decision",
            importance="important",
            target_path="02-Projects/bok-name.md",
        )
        result = self.service.propose_memory("产品名确定为 Bok。")
        self.assertEqual(result["status"], "pending")
        self.assertIn("important_memory", result["review_reasons"])
        with self.assertRaises(PermissionDeniedError):
            self.service.commit_memory(result["id"])
        committed = self.service.commit_memory(result["id"], confirm_important=True)
        self.assertEqual(committed["status"], "committed")

    def test_important_memory_rollback_requires_confirmation(self) -> None:
        self.service.memory.intelligence.analyze = lambda *args, **kwargs: ordinary_analysis(
            summary="Bok 正式产品名已经确定。",
            title="Bok 产品命名决策",
            memory_type="decision",
            importance="important",
            target_path="02-Projects/bok-name-rollback.md",
        )
        proposal = self.service.propose_memory("产品名确定为 Bok。")
        self.service.commit_memory(proposal["id"], confirm_important=True)
        with self.assertRaises(PermissionDeniedError):
            self.service.rollback_memory(proposal["id"])
        rolled = self.service.rollback_memory(proposal["id"], confirm_important=True)
        self.assertEqual(rolled["status"], "rolled_back")

    def test_existing_target_turns_create_into_safe_update(self) -> None:
        self.service.memory.intelligence.analyze = lambda *args, **kwargs: ordinary_analysis(
            summary="已有卡片的新补充。",
            target_path="03-Knowledge/retrieval.md",
            action="create",
        )
        result = self.service.propose_memory("给现有检索卡增加一个补充。")
        self.assertEqual(result["analysis"]["action"], "update")
        self.assertEqual(result["status"], "auto_committed")
        self.assertIn("Bok 更新记录", self.service.storage.read_text("03-Knowledge/retrieval.md"))

    def test_conflicting_memory_never_auto_commits(self) -> None:
        self.service.memory.intelligence.analyze = lambda *args, **kwargs: ordinary_analysis(action="conflict", memory_type="conflict", importance="important")
        result = self.service.propose_memory("这条内容与旧决策冲突。")
        self.assertTrue(result["requires_review"])
        self.assertIn("conflict", result["review_reasons"])

    def test_capture_returns_without_exposing_material(self) -> None:
        result = self.service.capture_memory("一段需要后台处理的内容")
        self.assertEqual(result["status"], "queued")
        self.assertNotIn("material", result)
        public = self.service.capture_status(result["id"])
        self.assertNotIn("material", public)

    def test_capture_queue_processes_when_model_is_available(self) -> None:
        self.service.memory.intelligence.analyze = lambda *args, **kwargs: ordinary_analysis(target_path="03-Knowledge/background.md")
        captured = self.service.capture_memory("后台安静整理这条知识")
        processed = self.service.process_captures(limit=1)
        self.assertEqual(processed["processed"][0]["status"], "completed")
        self.assertTrue((self.vault / "03-Knowledge/background.md").is_file())
        self.assertEqual(self.service.capture_status(captured["id"])["status"], "completed")

    def test_background_capture_batch_defers_then_analyzes_every_turn_once(self) -> None:
        captures = [self.service.capture_memory(f"批处理仍保留第 {index} 条独立原始证据") for index in range(10)]
        calls = []

        def fake_generate_json(**kwargs):
            calls.append(kwargs)
            payload = json.loads(kwargs["prompt"])
            self.assertEqual(len(payload["items"]), 10)
            return {
                "items": [
                    {
                        "capture_id": item["capture_id"],
                        "analysis": ordinary_analysis(
                            summary=f"第 {index} 条批处理结论。",
                            title=f"批处理结论 {index}",
                            target_path=f"03-Knowledge/batch-{index}.md",
                            source_excerpt=item["material"],
                        ),
                    }
                    for index, item in enumerate(payload["items"])
                ]
            }

        self.service.memory.intelligence.provider.generate_json = fake_generate_json
        processed = self.service.process_captures(limit=20, force=False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(processed["processed"]), 10)
        self.assertEqual({item["status"] for item in processed["processed"]}, {"completed"})
        self.assertEqual(processed["remaining"], 0)
        for index, captured in enumerate(captures):
            self.assertEqual(self.service.capture_status(captured["id"])["status"], "completed")
            self.assertTrue((self.vault / f"03-Knowledge/batch-{index}.md").is_file())

    def test_background_capture_batch_waits_briefly_without_dropping_turn(self) -> None:
        captured = self.service.capture_memory("不足十条时先等空闲窗口，不丢这一条")
        with patch.object(self.service.memory.intelligence, "analyze_many", side_effect=AssertionError("must defer")):
            deferred = self.service.process_captures(limit=20, force=False)
        self.assertEqual(deferred["processed"], [])
        self.assertEqual(deferred["deferred"], 1)
        self.assertGreater(deferred["next_batch_in_seconds"], 0)
        self.assertEqual(self.service.capture_status(captured["id"])["status"], "queued")

    def test_capture_queue_uses_one_private_record_per_item(self) -> None:
        captured = self.service.capture_memory("分片队列不会反复重写一个大文件")
        record = self.vault / ".bok/state/captures" / f"{captured['id']}.json"
        self.assertTrue(record.is_file())
        self.assertTrue((self.vault / ".bok/state/capture-pending" / f"{captured['id']}.json").is_file())
        self.assertTrue((self.vault / ".bok/state/capture-hashes" / captured["material_hash"][:2] / f"{captured['material_hash']}.json").is_file())
        self.assertFalse((self.vault / ".bok/state/capture-queue.json").exists())
        stored = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(stored["material"], "分片队列不会反复重写一个大文件")

    def test_new_capture_dedup_does_not_scan_all_capture_records(self) -> None:
        first = self.service.capture_memory("hash 指针提供常数时间精确去重")
        with patch.object(self.service.memory, "_load_captures", side_effect=AssertionError("full scan")):
            replay = self.service.capture_memory("hash 指针提供常数时间精确去重")
            second = self.service.capture_memory("新的正文同样不需要扫描旧分片")
        self.assertEqual(first["id"], replay["id"])
        self.assertNotEqual(first["id"], second["id"])

    def test_legacy_capture_queue_migrates_without_leaving_duplicate_raw_text(self) -> None:
        material = "legacy private material"
        capture_id = "a" * 32
        legacy_path = self.vault / ".bok/state/capture-queue.json"
        legacy_path.write_text(
            json.dumps(
                [
                    {
                        "id": capture_id,
                        "created_at": "2026-08-20T00:00:00Z",
                        "updated_at": "2026-08-20T00:00:00Z",
                        "status": "queued",
                        "material_hash": sha256_text(material),
                        "material": material,
                        "source": {"type": "test", "ref": "legacy", "at": "2026-08-20T00:00:00Z"},
                        "explicit_cloud_consent": False,
                        "attempts": 0,
                        "proposal_id": "",
                        "last_error": "",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        restarted = BokService(self.config)
        initialized = restarted.initialize()
        self.assertEqual(initialized["capture_markers"]["legacy_migrated"], 1)
        self.assertEqual(json.loads(legacy_path.read_text(encoding="utf-8")), [])
        shard = self.vault / ".bok/state/captures" / f"{capture_id}.json"
        self.assertEqual(json.loads(shard.read_text(encoding="utf-8"))["material"], material)

    def test_model_backoff_does_not_starve_newer_pending_captures(self) -> None:
        first = self.service.capture_memory("first waiting capture")
        second = self.service.capture_memory("second waiting capture")

        def unavailable(*_args, **_kwargs):
            raise BokError("provider_unavailable", "offline", status=503)

        self.service.memory.intelligence.analyze = unavailable
        self.service.process_captures(limit=1)
        self.service.process_captures(limit=1)
        first_status = self.service.capture_status(first["id"])
        second_status = self.service.capture_status(second["id"])
        self.assertEqual(first_status["status"], "waiting_for_model")
        self.assertEqual(second_status["status"], "waiting_for_model")
        self.assertTrue(first_status["next_attempt_at"])
        self.assertTrue(second_status["next_attempt_at"])
        self.assertEqual(first_status["attempts"], 1)
        self.assertEqual(second_status["attempts"], 1)

    def test_conversation_observe_writes_receipt_before_background_analysis(self) -> None:
        event = self.service.observe_conversation(
            conversation_id="chat-1",
            turn_id="turn-1",
            role="user",
            content="以后已有项目优先做最小范围修改。",
            client="test-client",
            agent="test-agent",
            project="02-Projects/bok.md",
        )
        self.assertEqual(event["status"], "queued_for_analysis")
        self.assertTrue(event["capture_id"])
        self.assertNotIn("content", event)
        self.assertEqual(event["capture_status"], "queued")
        stored = self.service.conversations._read(self.service.conversations._path(event["id"]))
        self.assertEqual(stored["content"], "以后已有项目优先做最小范围修改。")
        self.assertEqual(self.service.health()["conversation_ledger"]["queued_for_analysis"], 1)

    def test_conversation_turn_identity_is_idempotent_and_conflict_safe(self) -> None:
        first = self.service.observe_conversation(
            conversation_id="chat-2",
            turn_id="turn-1",
            role="user",
            content="同一轮只能落账一次。",
        )
        replay = self.service.observe_conversation(
            conversation_id="chat-2",
            turn_id="turn-1",
            role="user",
            content="同一轮只能落账一次。",
        )
        self.assertEqual(first["id"], replay["id"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(len(self.service.capture_status()["items"]), 1)
        with self.assertRaises(ConflictError):
            self.service.observe_conversation(
                conversation_id="chat-2",
                turn_id="turn-1",
                role="user",
                content="复用同一 turn id 但替换正文必须报冲突。",
            )

    def test_conversation_policy_separates_external_session_and_do_not_remember(self) -> None:
        external = self.service.observe_conversation(
            conversation_id="chat-policy",
            turn_id="external",
            role="user",
            content="这是引用材料，不代表用户本人。",
            external_content=True,
        )
        session = self.service.observe_conversation(
            conversation_id="chat-policy",
            turn_id="session",
            role="user",
            content="这条只在当前会话使用。",
            memory_mode="session_only",
        )
        forgotten = self.service.observe_conversation(
            conversation_id="chat-policy",
            turn_id="private",
            role="user",
            content="这条完全不要记。",
            memory_mode="do_not_remember",
        )
        self.assertEqual(external["status"], "excluded_external_content")
        self.assertEqual(session["status"], "session_only")
        self.assertEqual(forgotten["status"], "excluded_do_not_remember")
        self.assertFalse(external["capture_id"])
        self.assertFalse(session["capture_id"])
        stored = self.service.conversations._read(self.service.conversations._path(forgotten["id"]))
        self.assertNotIn("content", stored)
        self.assertFalse(stored["content_hash"])

    def test_conversation_reconcile_repairs_receipt_not_handed_to_queue(self) -> None:
        event = self.service.observe_conversation(
            conversation_id="chat-reconcile",
            turn_id="turn-1",
            role="user",
            content="先落账，之后可以补投分析队列。",
            memory_mode="session_only",
        )
        stored = self.service.conversations._read(self.service.conversations._path(event["id"]))
        stored["memory_mode"] = "default"
        stored["status"] = "received"
        self.service.conversations._write(stored)
        repaired = self.service.reconcile_conversations(limit=10)
        self.assertEqual(len(repaired["reconciled"]), 1)
        self.assertEqual(repaired["reconciled"][0]["status"], "queued_for_analysis")
        self.assertTrue(repaired["reconciled"][0]["capture_id"])

    def test_expired_conversation_content_is_purged_without_deleting_receipt(self) -> None:
        event = self.service.observe_conversation(
            conversation_id="chat-expire",
            turn_id="turn-1",
            role="user",
            content="临时内容到期后只保留收据。",
            memory_mode="session_only",
        )
        path = self.service.conversations._path(event["id"])
        stored = self.service.conversations._read(path)
        stored["content_expires_at"] = "2000-01-01T00:00:00Z"
        self.service.conversations._write(stored)
        self.assertEqual(self.service.purge_expired_conversation_content()["purged"], 1)
        purged = self.service.conversations._read(path)
        self.assertNotIn("content", purged)
        self.assertEqual(purged["status"], "expired_unprocessed")

    def test_expired_conversation_discards_unprocessed_capture_material(self) -> None:
        event = self.service.observe_conversation(
            conversation_id="chat-expire-capture",
            turn_id="turn-1",
            role="user",
            content="模型长期离线也不能让原文无限期留在捕获队列。",
        )
        event_path = self.service.conversations._path(event["id"])
        stored = self.service.conversations._read(event_path)
        stored["content_expires_at"] = "2000-01-01T00:00:00Z"
        self.service.conversations._write(stored)
        self.assertEqual(self.service.purge_expired_conversation_content()["purged"], 1)
        capture = self.service.capture_status(event["capture_id"])
        self.assertEqual(capture["status"], "discarded")
        capture_path = self.vault / ".bok/state/captures" / f"{event['capture_id']}.json"
        capture_record = json.loads(capture_path.read_text(encoding="utf-8"))
        self.assertNotIn("material", capture_record)

    def test_local_only_blocks_cloud_even_with_request_consent(self) -> None:
        policy = NetworkPolicy(local_only=True)
        with self.assertRaises(PermissionDeniedError):
            policy.require_allowed("https://example.com/v1/chat", explicit_cloud_consent=True)
        policy.require_allowed("http://127.0.0.1:11434/api/chat")

    def test_cloud_mode_still_requires_per_request_consent(self) -> None:
        policy = NetworkPolicy(local_only=False)
        with self.assertRaises(PermissionDeniedError):
            policy.require_allowed("https://example.com/v1/chat", explicit_cloud_consent=False)
        policy.require_allowed("https://example.com/v1/chat", explicit_cloud_consent=True)

    def test_byok_environment_reference_never_enters_config_value(self) -> None:
        store = CredentialStore(self.config)
        with patch.dict("os.environ", {"BOK_TEST_KEY": "secret-value"}, clear=False):
            self.assertEqual(store.get("env:BOK_TEST_KEY"), "secret-value")
        self.assertNotIn("secret-value", json.dumps(self.config.public_dict()))

    def test_model_expiration_hallucination_is_removed_for_policy(self) -> None:
        intelligence = MemoryIntelligence(self.config)
        result = intelligence.validate(ordinary_analysis(memory_type="policy", expires_at="2025-01-01T00:00:00Z"))
        self.assertEqual(result["expires_at"], "")

    def test_model_analysis_uses_mocked_provider_without_availability_preflight(self) -> None:
        intelligence = MemoryIntelligence(self.config)
        calls = []

        def fake_generate_json(**kwargs):
            calls.append(kwargs)
            return ordinary_analysis(source_excerpt="段落级检索")

        intelligence.provider.generate_json = fake_generate_json
        result = intelligence.analyze("段落级检索可以减少上下文。", nearby=[])
        self.assertEqual(result["action"], "create")
        self.assertEqual(len(calls), 1)

    def test_batch_analysis_falls_back_only_for_a_missing_item(self) -> None:
        intelligence = MemoryIntelligence(self.config)
        calls = []

        def fake_generate_json(**kwargs):
            calls.append(kwargs)
            payload = json.loads(kwargs["prompt"])
            if "items" in payload:
                first = payload["items"][0]
                return {
                    "items": [{
                        "capture_id": first["capture_id"],
                        "analysis": ordinary_analysis(source_excerpt=first["material"]),
                    }]
                }
            return ordinary_analysis(source_excerpt=payload["material"])

        intelligence.provider.generate_json = fake_generate_json
        entries = [
            {"id": "a" * 32, "material": "第一条批处理证据", "nearby": []},
            {"id": "b" * 32, "material": "第二条批处理证据", "nearby": []},
        ]
        result = intelligence.analyze_many(entries)
        self.assertEqual(set(result), {"a" * 32, "b" * 32})
        self.assertEqual(len(calls), 2)
        self.assertIn("output_schema", calls[0])
        self.assertNotIn("output_schema", calls[1])

    def test_backup_verify_and_restore(self) -> None:
        backup = self.service.backup_create()
        verification = self.service.backup_verify(backup["backup_id"])
        self.assertTrue(verification["valid"])
        target = self.vault / "03-Knowledge/retrieval.md"
        target.write_text("changed after backup", encoding="utf-8")
        extra = self.vault / "03-Knowledge/created-after-backup.md"
        extra.write_text("must disappear in exact mode", encoding="utf-8")
        restored = self.service.backup_restore(backup["backup_id"], confirm_vault=self.vault.name)
        self.assertTrue(restored["safety_backup"])
        self.assertEqual(restored["mode"], "exact")
        self.assertIn("混合检索", target.read_text(encoding="utf-8"))
        self.assertFalse(extra.exists())

    def test_merge_restore_preserves_markdown_created_after_backup(self) -> None:
        backup = self.service.backup_create()
        extra = self.vault / "03-Knowledge/merge-extra.md"
        extra.write_text("keep me", encoding="utf-8")
        restored = self.service.backup_restore(backup["backup_id"], confirm_vault=self.vault.name, mode="merge")
        self.assertEqual(restored["mode"], "merge")
        self.assertTrue(extra.is_file())

    def test_failed_restore_rolls_back_every_changed_document(self) -> None:
        first = self.vault / "02-Projects/bok.md"
        second = self.vault / "03-Knowledge/retrieval.md"
        backup = self.service.backup_create()
        first.write_text("current project state", encoding="utf-8")
        second.write_text("current retrieval state", encoding="utf-8")
        failed = False

        def fail_once(path, data, *, mode=0o600):
            nonlocal failed
            path = Path(path)
            if path == second and not failed:
                failed = True
                raise OSError("simulated restore failure")
            return real_atomic_write_bytes(path, data, mode=mode)

        with patch("bok_core.restore.atomic_write_bytes", side_effect=fail_once):
            with self.assertRaises(BokError) as caught:
                self.service.backup_restore(backup["backup_id"], confirm_vault=self.vault.name)
        self.assertEqual(caught.exception.code, "restore_failed")
        self.assertEqual(first.read_text(encoding="utf-8"), "current project state")
        self.assertEqual(second.read_text(encoding="utf-8"), "current retrieval state")
        self.assertEqual(list((self.vault / ".bok/restore-transactions").iterdir()), [])

    def test_startup_repair_rolls_back_an_interrupted_restore_transaction(self) -> None:
        target = self.vault / "03-Knowledge/interrupted.md"
        before = b"# Before interrupted restore\n"
        after = b"# Half-applied restore\n"
        target.write_bytes(after)
        transaction = self.vault / ".bok/restore-transactions/vault-restore-test"
        snapshot = transaction / "before/03-Knowledge/interrupted.md"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_bytes(before)
        (transaction / "journal.json").write_text(json.dumps({
            "transaction_id": "vault-restore-test",
            "namespace": "vault-restore",
            "status": "applying",
            "mode": "exact",
            "changes": [{
                "path": "03-Knowledge/interrupted.md",
                "before_exists": True,
                "before_hash": sha256_text(before.decode()),
                "after_exists": True,
                "after_hash": sha256_text(after.decode()),
            }],
        }), encoding="utf-8")
        repaired = self.service.storage.repair_restore_transactions()
        self.assertEqual(repaired["repaired"], 1)
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse(transaction.exists())

    def test_backup_list_does_not_depend_on_recent_activity_window(self) -> None:
        backup = self.service.backup_create()
        for index in range(120):
            self.service.storage._activity("test_noise", details={"index": index})
        listed = self.service.backup_list(limit=10)["items"]
        self.assertIn(backup["backup_id"], {item["backup_id"] for item in listed})

    def test_backup_identifier_cannot_escape_runtime_directory(self) -> None:
        with self.assertRaises(BokError) as caught:
            self.service.backup_verify("../../outside")
        self.assertEqual(caught.exception.code, "invalid_backup_id")

    def test_mcp_supports_latest_discovery_and_legacy_initialize(self) -> None:
        server = MCPServer(self.service)
        discovered = server._response({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}})
        self.assertIn("2026-07-28", discovered["result"]["supportedVersions"])
        initialized = server._response({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-06-18")
        tools = server._response({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        self.assertEqual(tools["result"]["cacheScope"], "private")
        self.assertTrue(tools["result"]["tools"][0]["annotations"]["readOnlyHint"])
        observed = server._response(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "bok_observe_conversation",
                    "arguments": {
                        "conversation_id": "mcp-token-contract",
                        "turn_id": "turn-1",
                        "role": "user",
                        "content": "这条原始证据只保存在本地收据中。",
                        "agent": "codex",
                    },
                },
            }
        )
        compact = observed["result"]["structuredContent"]
        self.assertEqual(compact, {"ok": True, "status": "queued_for_analysis", "personal": "disabled"})
        self.assertLess(len(observed["result"]["content"][0]["text"].encode("utf-8")), 120)
        self.assertNotIn("capture_id", observed["result"]["content"][0]["text"])
        detailed = self.service.conversation_status(conversation_id="mcp-token-contract", turn_id="turn-1")
        self.assertTrue(detailed["capture_id"])
        self.assertEqual(detailed["content_hash"], sha256_text("这条原始证据只保存在本地收据中。"))


class PersonalClaimContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.personal_temporary = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary.name).resolve()
        self.personal_root = Path(self.personal_temporary.name).resolve() / "Personal-Core"
        for name in ("00-System", "02-Projects", "03-Knowledge"):
            (self.vault / name).mkdir()
        (self.vault / "00-System/Active-Context.md").write_text("focus_path: 02-Projects/p.md\n", encoding="utf-8")
        (self.vault / "02-Projects/p.md").write_text("# P\n", encoding="utf-8")
        self.config = BokConfig(vault_root=self.vault, provider="none", port=0)
        self.service = BokService(self.config)
        self.service.initialize()

    def tearDown(self) -> None:
        self.personal_temporary.cleanup()
        self.temporary.cleanup()

    def configure(self) -> None:
        self.service.setup_personal_core(str(self.personal_root), confirm=True)

    def propose(self, statement="回答要直接，不要堆套话。", **overrides):
        values = {
            "statement": statement,
            "claim_type": "communication_preference",
            "source_refs": ["conversation:test:turn-1"],
        }
        values.update(overrides)
        return self.service.propose_person_claim(**values)

    def test_personal_core_is_safely_disabled_until_separate_folder_is_configured(self) -> None:
        health = self.service.person_health()
        self.assertFalse(health["configured"])
        self.assertFalse(health["ready"])
        with self.assertRaises(BokError) as caught:
            self.propose()
        self.assertEqual(caught.exception.code, "personal_core_not_configured")
        self.assertFalse((self.vault / "08-Person").exists())

    def test_personal_core_setup_is_explicit_persistent_and_does_not_expose_absolute_path(self) -> None:
        with self.assertRaises(BokError) as caught:
            self.service.setup_personal_core(str(self.personal_root))
        self.assertEqual(caught.exception.code, "personal_core_confirmation_required")
        result = self.service.setup_personal_core(str(self.personal_root), confirm=True)
        self.assertTrue(result["ready"])
        self.assertTrue((self.personal_root / "PERSONAL-CORE.md").is_file())
        self.assertTrue((self.personal_root / ".gitignore").is_file())
        config_data = json.loads((self.vault / ".bok/config.json").read_text(encoding="utf-8"))
        self.assertEqual(config_data["personal_core_root"], str(self.personal_root))
        self.assertNotIn(str(self.personal_root), json.dumps(result))
        self.assertNotIn(str(self.personal_root), json.dumps(self.service.health()))
        restarted = BokService(BokConfig.load(self.vault, {"provider": "none", "port": 0}))
        restarted.initialize()
        self.assertTrue(restarted.person_health()["ready"])

    def test_personal_core_rejects_project_paths_and_nonempty_unmarked_folders(self) -> None:
        with self.assertRaises(BokError) as caught:
            self.service.setup_personal_core(str(self.vault / "private"), confirm=True)
        self.assertEqual(caught.exception.code, "unsafe_personal_core")
        self.personal_root.mkdir()
        (self.personal_root / "unrelated.txt").write_text("do not touch", encoding="utf-8")
        with self.assertRaises(BokError) as caught:
            self.service.setup_personal_core(str(self.personal_root), confirm=True)
        self.assertEqual(caught.exception.code, "personal_core_not_empty")

    def test_personal_core_backup_is_separate_verified_and_safely_restorable(self) -> None:
        self.configure()
        proposed = self.propose()
        backup = self.service.person_backup_create()
        self.assertTrue(backup["backup_id"].startswith("personal-backup-"))
        self.assertFalse((self.vault / ".bok/backups" / f"{backup['backup_id']}.zip").exists())
        self.assertTrue((self.personal_root / ".bok/backups" / f"{backup['backup_id']}.zip").is_file())
        self.assertTrue(self.service.person_backup_verify(backup["backup_id"])["valid"])
        self.service.reject_person_claim(proposed["id"], reason="backup smoke")
        extra = self.personal_root / "Claims/person-00000000000000000000000000000000.md"
        extra.write_text("temporary extra", encoding="utf-8")
        with self.assertRaises(PermissionDeniedError):
            self.service.person_backup_restore(backup["backup_id"], confirm_personal_core="wrong")
        restored = self.service.person_backup_restore(
            backup["backup_id"],
            confirm_personal_core=self.personal_root.name,
        )
        self.assertTrue(restored["safety_backup"])
        self.assertFalse(extra.exists())
        self.assertEqual(self.service.person_claim(proposed["id"])["epistemic_status"], "explicit")
        listed = self.service.person_backup_list()["items"]
        self.assertGreaterEqual(len(listed), 2)
        self.assertTrue(all(item["valid"] for item in listed))

    def test_personal_backup_identifier_cannot_escape_private_runtime(self) -> None:
        self.configure()
        with self.assertRaises(BokError) as caught:
            self.service.person_backup_verify("../../outside")
        self.assertEqual(caught.exception.code, "invalid_personal_backup_id")

    def test_corrupt_personal_backup_returns_a_structured_error(self) -> None:
        self.configure()
        backup_id = "personal-backup-20260823T000000.000000Z-deadbeef"
        (self.personal_root / ".bok/backups" / f"{backup_id}.zip").write_bytes(b"not-a-zip")
        with self.assertRaises(BokError) as caught:
            self.service.person_backup_verify(backup_id)
        self.assertEqual(caught.exception.code, "personal_backup_corrupt")

    def test_claim_is_markdown_outside_project_and_requires_confirmation_before_context(self) -> None:
        self.configure()
        proposed = self.propose()
        self.assertEqual(proposed["epistemic_status"], "explicit")
        self.assertFalse(proposed["effective"])
        self.assertTrue((self.personal_root / proposed["path"]).is_file())
        self.assertFalse((self.vault / proposed["path"]).exists())
        before = self.service.person_context(task="调整回答风格", agent="codex")
        self.assertEqual(before["claims"], [])
        confirmed = self.service.confirm_person_claim(proposed["id"])
        self.assertTrue(confirmed["effective"])
        self.assertEqual(confirmed["access_scope"], ["personal-core"])
        self.assertEqual(self.service.person_context(task="调整回答风格", agent="codex")["claims"], [])
        self.service.authorize_person_claim(proposed["id"], access_scope=["agent:codex"])
        visible = self.service.person_context(task="调整回答风格", agent="codex")
        hidden = self.service.person_context(task="调整回答风格", agent="other")
        self.assertEqual(visible["claims"][0]["claim_id"], proposed["id"])
        self.assertEqual(hidden["claims"], [])
        self.assertLessEqual(visible["token_estimate"], visible["token_budget"])

    def test_authorization_changes_visibility_without_reconfirmation(self) -> None:
        self.configure()
        proposed = self.propose()
        self.service.confirm_person_claim(proposed["id"])
        self.service.authorize_person_claim(proposed["id"], access_scope=["agent:first"])
        changed = self.service.authorize_person_claim(proposed["id"], access_scope=["agent:second"])
        self.assertFalse(changed["unchanged"])
        self.assertEqual(changed["access_scope"], ["agent:second"])
        self.assertEqual(self.service.person_context(task="回答", agent="first")["claims"], [])
        self.assertEqual(len(self.service.person_context(task="回答", agent="second")["claims"]), 1)

    def test_agent_access_requires_a_separate_post_confirmation_action(self) -> None:
        self.configure()
        with self.assertRaises(BokError) as caught:
            self.propose(access_scope=["all-agents"])
        self.assertEqual(caught.exception.code, "authorization_requires_confirmed_claim")
        proposed = self.propose()
        with self.assertRaises(ConflictError):
            self.service.authorize_person_claim(proposed["id"], access_scope=["all-agents"])
        confirmed = self.service.confirm_person_claim(proposed["id"])
        self.assertEqual(confirmed["access_scope"], ["personal-core"])

    def test_rejected_claim_is_guarded_from_silent_recreation_and_never_injected(self) -> None:
        self.configure()
        proposed = self.propose()
        rejected = self.service.reject_person_claim(proposed["id"], reason="这不是稳定偏好")
        self.assertEqual(rejected["epistemic_status"], "rejected")
        duplicate = self.propose()
        self.assertEqual(duplicate["id"], proposed["id"])
        self.assertTrue(duplicate["rejected_guard"])
        self.assertEqual(self.service.person_context(task="回答", agent="codex")["claims"], [])

    def test_correction_is_versioned_explainable_and_reversible(self) -> None:
        self.configure()
        proposed = self.propose()
        self.service.confirm_person_claim(proposed["id"])
        corrected = self.service.correct_person_claim(
            proposed["id"],
            statement="回答先给结论，再给必要依据。",
            source_ref="conversation:test:turn-2",
        )
        explanation = self.service.explain_person_claim(proposed["id"])
        self.assertIn("conversation:test:turn-2", explanation["explanation"]["sources"])
        self.assertIn("回答要直接，不要堆套话。", explanation["statement_history"])
        guarded = self.propose()
        self.assertEqual(guarded["id"], proposed["id"])
        self.assertTrue(guarded["historical_guard"])
        versions = self.service.person_claim_versions(proposed["id"])["items"]
        self.assertTrue(any(item["version_id"] == corrected["version_id"] for item in versions))
        with self.assertRaises(PermissionDeniedError):
            self.service.rollback_person_claim(corrected["version_id"])
        rolled = self.service.rollback_person_claim(corrected["version_id"], confirm_important=True)
        self.assertEqual(rolled["statement"], "回答要直接，不要堆套话。")

    def test_supersede_preserves_bidirectional_lineage_and_restart_repairs_partial_link(self) -> None:
        self.configure()
        proposed = self.propose()
        self.service.confirm_person_claim(proposed["id"])
        changed = self.service.supersede_person_claim(
            proposed["id"],
            statement="回答保持自然，但技术结论必须完整。",
            source_ref="conversation:test:turn-3",
        )
        old = self.service.person_claim(proposed["id"])
        replacement = changed["replacement"]
        self.assertEqual(old["superseded_by"], replacement["id"])
        self.assertEqual(replacement["supersedes"], old["id"])
        self.assertFalse(old["effective"])
        raw_old = self.service.person._read(old["id"])
        raw_old["epistemic_status"] = "confirmed"
        raw_old["confirmed_by_user"] = True
        raw_old["superseded_by"] = ""
        raw_old["valid_to"] = ""
        (self.personal_root / raw_old["path"]).write_text(self.service.person._render(raw_old), encoding="utf-8")
        restarted = BokService(BokConfig.load(self.vault, {"provider": "none", "port": 0}))
        restarted.initialize()
        repaired = restarted.person_claim(old["id"])
        self.assertEqual(repaired["epistemic_status"], "superseded")
        self.assertEqual(repaired["superseded_by"], replacement["id"])

    def test_task_scopes_and_token_budget_keep_person_context_minimal(self) -> None:
        self.configure()
        matching = self.propose(
            "写代码时先跑测试。",
            claim_type="work_preference",
            scope_kind="task_type",
            scope_value="coding tests",
        )
        self.service.confirm_person_claim(matching["id"])
        self.service.authorize_person_claim(matching["id"], access_scope=["all-agents"])
        self.assertEqual(self.service.person_context(task="coding tests", agent="codex")["claims"][0]["claim_id"], matching["id"])
        self.assertEqual(self.service.person_context(task="规划旅行", agent="codex")["claims"], [])

    def test_person_context_is_available_as_read_only_mcp_tool(self) -> None:
        self.configure()
        proposed = self.propose()
        self.service.confirm_person_claim(proposed["id"])
        self.service.authorize_person_claim(proposed["id"], access_scope=["all-agents"])
        server = MCPServer(self.service)
        listed = server._response({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        tool = next(item for item in listed["result"]["tools"] if item["name"] == "bok_person_context")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        called = server._response(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "bok_person_context", "arguments": {"task": "回答", "agent": "codex"}},
            }
        )
        self.assertFalse(called["result"]["isError"])
        self.assertEqual(called["result"]["structuredContent"]["claims"][0]["claim_id"], proposed["id"])

    def test_tampered_or_symlinked_claims_fail_closed(self) -> None:
        self.configure()
        proposed = self.propose()
        claim_path = self.personal_root / proposed["path"]
        claim_path.write_text(
            claim_path.read_text(encoding="utf-8").replace("epistemic_status: explicit", "epistemic_status: confirmed"),
            encoding="utf-8",
        )
        health = self.service.person_health()
        self.assertFalse(health["ready"])
        self.assertEqual(health["corrupt_claims"], 1)
        claim_path.unlink()
        outside = Path(self.personal_temporary.name) / "outside.md"
        outside.write_text("do not read", encoding="utf-8")
        claim_path.symlink_to(outside)
        with self.assertRaises(BokError) as caught:
            self.service.person_claim(proposed["id"])
        self.assertEqual(caught.exception.code, "unsafe_personal_claim")

    def test_interrupted_version_journal_is_reconciled_without_guessing(self) -> None:
        self.configure()
        proposed = self.propose()
        meta_path = self.personal_root / ".bok/versions" / proposed["version_id"] / "meta.json"
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        metadata["status"] = "pending"
        meta_path.write_text(json.dumps(metadata), encoding="utf-8")
        restarted = BokService(BokConfig.load(self.vault, {"provider": "none", "port": 0}))
        restarted.initialize()
        self.assertEqual(json.loads(meta_path.read_text(encoding="utf-8"))["status"], "committed")
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        metadata["status"] = "pending"
        metadata["after_hash"] = "0" * 64
        meta_path.write_text(json.dumps(metadata), encoding="utf-8")
        restarted.initialize()
        self.assertEqual(json.loads(meta_path.read_text(encoding="utf-8"))["status"], "aborted")

    def test_forget_transaction_repair_finishes_staged_erasure_and_rolls_back_partial_staging(self) -> None:
        self.configure()
        first = self.service.propose_person_claim(
            statement="第一条遗忘事务修复测试。",
            claim_type="knowledge_claim",
            source_refs=["test:forget-repair-1"],
        )
        first_path = self.personal_root / first["path"]
        completed = self.personal_root / ".bok/forget-transactions/forget-complete"
        completed_staged = completed / "staged/000000"
        completed_staged.parent.mkdir(parents=True)
        first_path.replace(completed_staged)
        (completed / "journal.json").write_text(json.dumps({
            "kind": "personal-claim-forget",
            "transaction_id": "forget-complete",
            "claim_id": first["id"],
            "status": "staged",
            "items": [{"source": first["path"], "staged": "staged/000000"}],
        }), encoding="utf-8")

        second = self.service.propose_person_claim(
            statement="第二条遗忘事务修复测试。",
            claim_type="knowledge_claim",
            source_refs=["test:forget-repair-2"],
        )
        second_path = self.personal_root / second["path"]
        partial = self.personal_root / ".bok/forget-transactions/forget-partial"
        partial_staged = partial / "staged/000000"
        partial_staged.parent.mkdir(parents=True)
        second_path.replace(partial_staged)
        (partial / "journal.json").write_text(json.dumps({
            "kind": "personal-claim-forget",
            "transaction_id": "forget-partial",
            "claim_id": second["id"],
            "status": "staging",
            "items": [{"source": second["path"], "staged": "staged/000000"}],
        }), encoding="utf-8")

        repaired = self.service.person.repair_forget_transactions()
        self.assertEqual(repaired["completed"], 1)
        self.assertEqual(repaired["rolled_back"], 1)
        self.assertFalse(first_path.exists())
        self.assertTrue(second_path.is_file())
        self.assertFalse(completed.exists())
        self.assertFalse(partial.exists())

    def test_linked_claim_cannot_be_rolled_back_independently(self) -> None:
        self.configure()
        proposed = self.propose()
        self.service.confirm_person_claim(proposed["id"])
        self.service.supersede_person_claim(
            proposed["id"],
            statement="回答自然、完整并保持必要细节。",
            source_ref="conversation:test:turn-4",
        )
        old_versions = self.service.person_claim_versions(proposed["id"])["items"]
        linked_version = next(item for item in old_versions if item["operation"] == "person_claim_supersede_old")
        with self.assertRaises(ConflictError):
            self.service.rollback_person_claim(linked_version["version_id"], confirm_important=True)

    def test_person_claim_cache_reuses_parsed_markdown_and_detects_external_edits(self) -> None:
        self.configure()
        proposed = self.propose()
        self.service.person_claims()
        with patch.object(self.service.person, "_record", wraps=self.service.person._record) as parser:
            self.service.person_claims()
            self.assertEqual(parser.call_count, 0)
        claim_path = self.personal_root / proposed["path"]
        claim_path.write_text(
            claim_path.read_text(encoding="utf-8").replace("epistemic_status: explicit", "epistemic_status: invalid"),
            encoding="utf-8",
        )
        self.assertFalse(self.service.person_health()["ready"])

    def test_person_claim_rejects_empty_visibility_principals(self) -> None:
        self.configure()
        with self.assertRaises(BokError) as caught:
            self.propose(access_scope=["agent:"])
        self.assertEqual(caught.exception.code, "invalid_access_scope")

    def test_person_claim_rollback_rejects_a_corrupted_snapshot(self) -> None:
        self.configure()
        proposed = self.propose()
        self.service.confirm_person_claim(proposed["id"])
        corrected = self.service.correct_person_claim(
            proposed["id"],
            statement="回答只保留结论。",
            source_ref="conversation:test:turn-corrupt",
        )
        before_path = self.personal_root / ".bok/versions" / corrected["version_id"] / "before.md"
        before_path.write_text("tampered", encoding="utf-8")
        with self.assertRaises(ConflictError):
            self.service.rollback_person_claim(corrected["version_id"], confirm_important=True)


class PersonalLearningContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.personal_temporary = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary.name).resolve()
        self.personal_root = Path(self.personal_temporary.name).resolve() / "Personal-Core"
        for name in ("00-System", "02-Projects", "03-Knowledge"):
            (self.vault / name).mkdir()
        (self.vault / "00-System/Active-Context.md").write_text("focus_path: 02-Projects/p.md\n", encoding="utf-8")
        (self.vault / "02-Projects/p.md").write_text("# P\n\n## 下一步行动\n\n- Test\n", encoding="utf-8")
        self.service = BokService(BokConfig(vault_root=self.vault, provider="none", port=0))
        self.service.initialize()
        self.service.setup_personal_core(str(self.personal_root), confirm=True)

    def tearDown(self) -> None:
        self.personal_temporary.cleanup()
        self.temporary.cleanup()

    def _confirmed_claim(self, statement="回答先说结论。") -> dict:
        proposed = self.service.propose_person_claim(
            statement=statement,
            claim_type="communication_preference",
            source_refs=["test:explicit"],
        )
        self.service.confirm_person_claim(proposed["id"])
        return self.service.authorize_person_claim(proposed["id"], access_scope=["all-agents"])

    def test_explicit_low_risk_turn_becomes_quiet_learned_understanding(self) -> None:
        signal = {
            "candidate_statement": "用户偏好回答先给结论，再补充必要依据。",
            "claim_type": "communication_preference",
            "signal_kind": "explicit",
            "polarity": "support",
            "scope_kind": "global",
            "confidence": 0.98,
            "inference_basis": "用户明确表达了稳定的回答结构偏好",
            "concept_key": "communication.conclusion-first",
        }
        receipt = self.service.observe_conversation(
            conversation_id="chat-explicit",
            turn_id="turn-1",
            role="user",
            content="回答不要堆套话，先说结论。",
            agent="codex",
            personal_signals=[signal],
        )
        self.assertEqual(receipt["personal_learning"]["status"], "observed")
        observation = self.service.person_observations()["items"][0]
        self.assertEqual(observation["candidate_statement"], signal["candidate_statement"])
        self.assertFalse(observation.get("source_excerpt"))
        self.assertEqual(str(observation["interpretation_version"]), "2")
        result = self.service.process_person_learning(limit=20)
        self.assertEqual(result["projected"], 1)
        self.assertEqual(result["learned"], 1)
        dashboard = self.service.person_dashboard()
        self.assertEqual(dashboard["claims"]["review_required"], [])
        self.assertEqual(len(dashboard["claims"]["understanding"]), 1)
        learned = dashboard["claims"]["understanding"][0]
        self.assertEqual(learned["epistemic_status"], "learned")
        self.assertFalse(learned["confirmed_by_user"])
        self.assertTrue(learned["effective"])
        self.assertEqual(learned["access_scope"], ["personal-core"])
        context = self.service.person_context(task="调整回答方式", agent="codex")
        self.assertEqual(context["claims"][0]["claim_id"], learned["id"])
        self.assertEqual(context["claims"][0]["why"], "learned_scope_and_task_match")
        self.assertEqual(dashboard["claims"]["profile"][0]["label"], "沟通方式")

    def test_upgrade_migrates_old_safe_review_card_without_user_confirmation(self) -> None:
        legacy = self.service.propose_person_claim(
            statement="用户偏好直接看到结果和必要依据。",
            claim_type="communication_preference",
            confidence=0.97,
            sensitivity="private",
            source_refs=["conversation:legacy-chat:turn-1"],
        )
        self.assertEqual(legacy["epistemic_status"], "explicit")
        self.assertFalse(legacy["effective"])
        result = self.service.initialize()
        self.assertEqual(result["personal_learning"]["upgrade_promoted"], 1)
        migrated = self.service.person_claim(legacy["id"])
        self.assertEqual(migrated["epistemic_status"], "learned")
        self.assertFalse(migrated["confirmed_by_user"])
        self.assertTrue(migrated["effective"])
        self.assertEqual(self.service.person_dashboard()["claims"]["review_required"], [])

    def test_upgrade_never_migrates_protected_or_sensitive_review_cards(self) -> None:
        identity = self.service.propose_person_claim(
            statement="用户提供了一项长期身份信息。",
            claim_type="identity",
            confidence=0.99,
            sensitivity="private",
            source_refs=["conversation:legacy-chat:turn-1"],
        )
        sensitive = self.service.propose_person_claim(
            statement="用户可能有一项敏感的沟通偏好。",
            claim_type="communication_preference",
            confidence=0.99,
            sensitivity="sensitive",
            source_refs=["conversation:legacy-chat:turn-2"],
        )
        result = self.service.initialize()
        self.assertEqual(result["personal_learning"]["upgrade_promoted"], 0)
        self.assertEqual(self.service.person_claim(identity["id"])["epistemic_status"], "explicit")
        self.assertEqual(self.service.person_claim(sensitive["id"])["epistemic_status"], "explicit")

    def test_turn_without_model_interpretation_does_not_copy_user_wording(self) -> None:
        receipt = self.service.observe_conversation(
            conversation_id="chat-no-signal",
            turn_id="turn-1",
            role="user",
            content="现在先把这个项目改完，暂时不要提交。",
            agent="codex",
        )
        self.assertEqual(receipt["personal_learning"]["status"], "no_signal")
        self.assertEqual(self.service.person_observations()["items"], [])

    def test_verbatim_personal_signal_is_rejected_without_persistence(self) -> None:
        content = "开始全部完善，但不要提交 GitHub，做完以后再优化 UI。"
        receipt = self.service.observe_conversation(
            conversation_id="chat-verbatim",
            turn_id="turn-1",
            role="user",
            content=content,
            agent="codex",
            personal_signals=[{
                "candidate_statement": content,
                "claim_type": "authority_rule",
                "signal_kind": "explicit",
                "polarity": "support",
                "scope_kind": "global",
                "confidence": 0.98,
            }],
        )
        self.assertEqual(receipt["personal_learning"]["status"], "needs_attention")
        self.assertEqual(receipt["personal_learning"]["rejected_signals"][0]["reason"], "interpretation_must_be_third_person")
        self.assertEqual(self.service.person_observations()["items"], [])

    def test_observed_pattern_becomes_learned_after_three_conversations_and_two_contexts(self) -> None:
        signal = {
            "candidate_statement": "用户在编码任务中倾向先运行测试，再继续修改。",
            "claim_type": "work_preference",
            "signal_kind": "observed",
            "polarity": "support",
            "scope_kind": "task_type",
            "scope_value": "coding tests",
            "confidence": 0.8,
            "inference_basis": "多次编码任务中出现相同的执行顺序选择",
            "concept_key": "work.coding-test-first",
        }
        statements = (
            "用户在编码任务中倾向先运行测试，再继续修改。",
            "用户处理代码变更时更习惯先验证现状，再动手调整。",
            "用户在开发工作中倾向把基线测试放在修改之前。",
        )
        for index, project in enumerate(("project-a", "project-b", "project-a"), 1):
            self.service.observe_conversation(
                conversation_id=f"chat-{index}",
                turn_id="turn-1",
                role="user",
                content="今天继续写代码。",
                agent="codex",
                project=project,
                personal_signals=[{**signal, "candidate_statement": statements[index - 1]}],
            )
        result = self.service.process_person_learning(limit=20)
        self.assertEqual(result["projected"], 1)
        candidate = self.service.person_dashboard()["claims"]["understanding"][0]
        self.assertEqual(candidate["epistemic_status"], "learned")
        self.assertEqual(candidate["support_count"], 3)
        self.assertTrue(candidate["effective"])

    def test_low_confidence_preference_accumulates_without_interrupting_user(self) -> None:
        self.service.observe_conversation(
            conversation_id="chat-low-confidence",
            turn_id="turn-1",
            role="user",
            content="这次可以少写一点。",
            agent="codex",
            personal_signals=[{
                "candidate_statement": "用户可能偏好在部分场景中使用更紧凑的回答。",
                "claim_type": "communication_preference",
                "signal_kind": "explicit",
                "polarity": "support",
                "scope_kind": "context",
                "scope_value": "compact replies",
                "confidence": 0.7,
                "inference_basis": "单次表达可能反映局部回答长度偏好",
                "concept_key": "communication.compact-contextual",
            }],
        )
        result = self.service.process_person_learning(limit=20)
        self.assertEqual(result["projected"], 0)
        dashboard = self.service.person_dashboard()
        self.assertEqual(dashboard["claims"]["total"], 0)
        self.assertEqual(dashboard["claims"]["review_required"], [])
        self.assertEqual(dashboard["observations"]["counts"]["accumulating"], 1)

    def test_identity_and_authority_still_require_user_intervention(self) -> None:
        self.service.observe_conversation(
            conversation_id="chat-identity",
            turn_id="turn-1",
            role="user",
            content="这是一个需要长期记住的身份说明。",
            agent="codex",
            personal_signals=[{
                "candidate_statement": "用户提出了一项可能影响长期回答的身份信息。",
                "claim_type": "identity",
                "signal_kind": "explicit",
                "polarity": "support",
                "scope_kind": "global",
                "confidence": 0.99,
                "inference_basis": "用户直接提供了长期身份说明",
                "concept_key": "identity.long-term-description",
            }],
        )
        self.service.process_person_learning(limit=20)
        dashboard = self.service.person_dashboard()
        self.assertEqual(dashboard["claims"]["understanding"], [])
        self.assertEqual(dashboard["claims"]["review_required"][0]["claim_type"], "identity")
        self.assertFalse(dashboard["claims"]["review_required"][0]["effective"])

    def test_contradiction_suspends_quiet_understanding_until_review(self) -> None:
        signal = {
            "candidate_statement": "用户偏好先看到结论，再阅读必要说明。",
            "claim_type": "communication_preference",
            "signal_kind": "explicit",
            "polarity": "support",
            "scope_kind": "global",
            "confidence": 0.98,
            "inference_basis": "用户明确表达了稳定的回答结构偏好",
            "concept_key": "communication.conclusion-first",
        }
        self.service.observe_conversation(
            conversation_id="chat-support",
            turn_id="turn-1",
            role="user",
            content="回答时先告诉我结果。",
            agent="codex",
            personal_signals=[signal],
        )
        self.service.process_person_learning(limit=20)
        learned = self.service.person_dashboard()["claims"]["understanding"][0]
        self.service.observe_conversation(
            conversation_id="chat-contradict",
            turn_id="turn-1",
            role="user",
            content="这个场景不要先说结论。",
            agent="codex",
            personal_signals=[{
                **signal,
                "candidate_statement": "用户在新的回答场景中否定了先给结论的既有偏好。",
                "polarity": "contradict",
                "claim_id": learned["id"],
                "inference_basis": "用户直接纠正了既有回答结构偏好",
            }],
        )
        self.service.process_person_learning(limit=20)
        dashboard = self.service.person_dashboard()
        self.assertEqual(dashboard["claims"]["understanding"], [])
        conflicted = dashboard["claims"]["review_required"][0]
        self.assertEqual(conflicted["epistemic_status"], "contradicted")
        self.assertFalse(conflicted["effective"])
        self.assertEqual(self.service.person_context(task="回答", agent="codex")["claims"], [])

    def test_sensitive_turn_keeps_content_free_receipts(self) -> None:
        secret = "sk-abcdefghijk12345"
        receipt = self.service.observe_conversation(
            conversation_id="chat-secret",
            turn_id="turn-1",
            role="user",
            content=f"我的 API key 是 {secret}",
            agent="codex",
        )
        self.assertTrue(receipt["privacy_filtered"])
        self.assertEqual(receipt["status"], "excluded_do_not_remember")
        observations = self.service.person_observations()["items"]
        self.assertEqual(observations[0]["status"], "excluded_sensitive")
        all_personal_text = "\n".join(path.read_text(encoding="utf-8") for path in self.personal_root.rglob("*.md"))
        all_runtime_text = "\n".join(path.read_text(encoding="utf-8") for path in (self.vault / ".bok").rglob("*.json"))
        self.assertNotIn(secret, all_personal_text)
        self.assertNotIn(secret, all_runtime_text)

    def test_impact_and_negative_outcome_feed_cleanup_without_answer_body(self) -> None:
        claim = self._confirmed_claim()
        impact = self.service.record_person_impact(
            answer_ref="answer-1",
            task="给出项目建议",
            agent="codex",
            claim_ids=[claim["id"]],
        )
        self.assertNotIn("answer_body", impact)
        outcome = self.service.record_person_outcome(
            answer_ref="answer-1",
            outcome="negative",
            agent="codex",
            claim_ids=[claim["id"]],
            source_ref="user:feedback-1",
            rework=True,
            note="仍然太啰嗦",
        )
        self.assertEqual(outcome["outcome"], "negative")
        candidate = next(item for item in self.service.person_cleanup_candidates()["items"] if item["claim_id"] == claim["id"])
        self.assertIn("negative_outcomes", candidate["reasons"])

    def test_cleanup_never_expires_important_claim_without_confirmation(self) -> None:
        claim = self._confirmed_claim()
        with self.assertRaises(PermissionDeniedError):
            self.service.person_cleanup_action(claim["id"], action="expire")
        expired = self.service.person_cleanup_action(claim["id"], action="expire", confirm_important=True)
        self.assertEqual(expired["claim"]["epistemic_status"], "expired")

    def test_forget_claim_erases_raw_turn_derivations_versions_and_matching_backups(self) -> None:
        original = "这是一句只用于遗忘回归测试的原始表达。"
        interpretation = "用户要求个人记忆保存抽象理解，而不是照抄对话原句。"
        receipt = self.service.observe_conversation(
            conversation_id="chat-forget",
            turn_id="turn-1",
            role="user",
            content=original,
            agent="codex",
            personal_signals=[{
                "candidate_statement": interpretation,
                "claim_type": "authority_rule",
                "signal_kind": "explicit",
                "polarity": "support",
                "scope_kind": "global",
                "confidence": 0.99,
                "inference_basis": "用户纠正了个人记忆的长期沉淀方式",
                "concept_key": "memory.semantic-abstraction",
            }],
        )
        analysis = {
            "action": "create",
            "title": "遗忘测试派生知识",
            "summary": "这条知识来自即将被遗忘的回合。",
            "reason": "遗忘测试",
            "memory_type": "knowledge",
            "importance": "ordinary",
            "sensitivity": "none",
            "confidence": 0.99,
            "target_path": "03-Knowledge/forget-derived.md",
            "tags": ["test"],
            "expires_at": "",
        }
        with patch.object(self.service.memory.intelligence, "analyze", return_value=analysis):
            processed_capture = self.service.process_captures(limit=1)["processed"][0]
        proposal_id = processed_capture["proposal_id"]
        self.assertEqual(self.service.memory.get(proposal_id)["status"], "auto_committed")
        self.service.process_person_learning(limit=20)
        claim = self.service.person_dashboard()["claims"]["pending"][0]
        self.service.confirm_person_claim(claim["id"])
        self.service.authorize_person_claim(claim["id"], access_scope=["all-agents"])
        self.service.record_person_impact(
            answer_ref="forget-answer",
            task="验证遗忘",
            agent="codex",
            claim_ids=[claim["id"]],
        )
        self.service.record_person_outcome(
            answer_ref="forget-answer",
            outcome="negative",
            agent="codex",
            claim_ids=[claim["id"]],
            source_ref="user:forget-feedback",
        )
        backup = self.service.person_backup_create()
        with self.assertRaises(PermissionDeniedError):
            self.service.forget_person_claim(claim["id"])
        result = self.service.forget_person_claim(claim["id"], confirm_forget=True)
        self.assertTrue(result["forgotten"])
        self.assertIn(backup["backup_id"], result["removed_backups"])
        self.assertEqual(result["removed_learning_records"], {"observations": 1, "outcomes": 1, "impacts": 1})
        self.assertEqual(result["forgotten_source_turns"][0]["event_id"], receipt["id"])
        self.assertEqual(result["derived_memory_requiring_review"], ["03-Knowledge/forget-derived.md"])
        sanitized_proposal = self.service.memory.get(proposal_id)
        self.assertEqual(sanitized_proposal["status"], "forgotten_source")
        self.assertNotIn("analysis", sanitized_proposal)
        self.assertNotIn("material_hash", sanitized_proposal)
        derived_path = self.vault / sanitized_proposal["target_path"]
        version_dir = self.vault / ".bok/versions" / sanitized_proposal["version_id"]
        derived_path.unlink()
        (version_dir / "meta.json").unlink()
        version_dir.rmdir()
        capture_id = result["forgotten_source_turns"][0]["capture"]["capture_id"]
        self.service.memory.forget_capture(capture_id)
        fully_sanitized = self.service.memory.get(proposal_id)
        self.assertEqual(fully_sanitized["status"], "forgotten")
        self.assertEqual(fully_sanitized["target_path"], "")
        self.assertEqual(fully_sanitized["version_id"], "")
        self.assertNotIn("forget-derived.md", (self.vault / ".bok/activity.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(self.service.person_observations()["items"], [])
        self.assertEqual(self.service.person_backup_list()["items"], [])
        with self.assertRaises(NotFoundError):
            self.service.person_claim(claim["id"])
        private_text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in self.personal_root.rglob("*") if path.is_file()
        )
        runtime_text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in (self.vault / ".bok").rglob("*") if path.is_file()
        )
        self.assertNotIn(original, private_text + runtime_text)
        self.assertNotIn(interpretation, private_text + runtime_text)

    def test_agent_credentials_are_hashed_scoped_and_revocable(self) -> None:
        issued = self.service.issue_agent_credential("codex", scopes=["context:read", "outcome:write"])
        stored = (self.vault / ".bok/state/agent-credentials.json").read_text(encoding="utf-8")
        self.assertNotIn(issued["token"], stored)
        principal = self.service.authenticate_agent(issued["token"])
        self.assertEqual(principal["agent_id"], "codex")
        self.assertEqual(set(principal["scopes"]), {"context:read", "outcome:write"})
        self.service.revoke_agent_credential("codex")
        self.assertIsNone(self.service.authenticate_agent(issued["token"]))

    def test_observation_is_idempotent_and_dashboard_has_all_visual_layers(self) -> None:
        values = {
            "conversation_id": "chat-once",
            "turn_id": "turn-1",
            "role": "user",
            "content": "我喜欢先看结论。",
            "agent": "codex",
            "personal_signals": [{
                "candidate_statement": "用户明确偏好先看到结论，再阅读展开说明。",
                "claim_type": "communication_preference",
                "signal_kind": "explicit",
                "polarity": "support",
                "scope_kind": "global",
                "confidence": 0.98,
                "inference_basis": "用户明确表达了稳定的回答结构偏好",
                "concept_key": "communication.conclusion-first",
            }],
        }
        first = self.service.observe_conversation(**values)
        second = self.service.observe_conversation(**values)
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(first["personal_learning"]["observations"][0]["id"], second["personal_learning"]["observations"][0]["id"])
        dashboard = self.service.person_dashboard()
        self.assertIn("observations", dashboard)
        self.assertIn("outcomes", dashboard)
        self.assertIn("impacts", dashboard)
        self.assertIn("cleanup", dashboard)
        self.assertIn("timeline", dashboard)
        self.assertIn("permissions", dashboard)
        self.assertEqual(dashboard["permissions"]["default"], "personal-core")


class BokAPIContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.personal_temporary = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary.name).resolve()
        self.personal_root = Path(self.personal_temporary.name).resolve() / "Personal-Core"
        for name in ("00-System", "02-Projects", "03-Knowledge"):
            (self.vault / name).mkdir()
        (self.vault / "00-System/Active-Context.md").write_text("focus_path: 02-Projects/p.md\n", encoding="utf-8")
        (self.vault / "02-Projects/p.md").write_text("# P\n\n## 下一步\n\n- Test\n", encoding="utf-8")
        self.service = BokService(BokConfig(vault_root=self.vault, provider="none", port=0))
        self.service.initialize()
        self.server = BokAPIServer(("127.0.0.1", 0), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base = f"http://{host}:{port}"
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.personal_temporary.cleanup()
        self.temporary.cleanup()

    def request(self, route: str, *, body=None, token=True, idempotency=""):
        headers = {}
        data = None
        if token:
            credential = self.server.token if token is True else str(token)
            headers["Authorization"] = f"Bearer {credential}"
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        return self.opener.open(Request(self.base + route, data=data, headers=headers, method="POST" if body is not None else "GET"), timeout=10)

    def test_api_requires_bearer_token(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            self.request("/v1/health", token=False)
        self.assertEqual(caught.exception.code, 401)

    def test_background_controls_require_admin_and_persist_valid_limits(self) -> None:
        issued = self.service.issue_agent_credential("test-agent", scopes=["vault:read"])
        for token, expected in ((False, 401), (issued["token"], 403)):
            for body in (None, {"paused": True}):
                with self.subTest(token=bool(token), body=body), self.assertRaises(HTTPError) as caught:
                    self.request("/v1/background", body=body, token=token)
                self.assertEqual(caught.exception.code, expected)
        with self.request("/v1/background", body={"paused": True, "batch_limit": 2, "interval_seconds": 60}) as response:
            saved = json.load(response)
        with self.request("/v1/background") as response:
            self.assertEqual(json.load(response), saved)
        with self.assertRaises(HTTPError) as caught:
            self.request("/v1/background", body={"paused": "false"})
        self.assertEqual(caught.exception.code, 400)
        self.assertTrue(self.service.background.status()["paused"])

    def test_health_is_authenticated_and_local(self) -> None:
        with self.request("/v1/health") as response:
            payload = json.load(response)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["service"], "bok-memory")
        self.assertTrue(payload["local_only"])

    def test_idempotency_prevents_duplicate_quick_notes(self) -> None:
        body = {"text": "Only once", "source": "test"}
        with self.request("/v1/quick-notes", body=body, idempotency="note-once") as response:
            first = json.load(response)
        with self.request("/v1/quick-notes", body=body, idempotency="note-once") as response:
            second = json.load(response)
        self.assertEqual(first["path"], second["path"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(len(list((self.vault / "07-Quick-Notes").glob("*.md"))), 1)

    def test_idempotency_key_cannot_change_request(self) -> None:
        with self.request("/v1/quick-notes", body={"text": "one"}, idempotency="same-key"):
            pass
        with self.assertRaises(HTTPError) as caught:
            self.request("/v1/quick-notes", body={"text": "two"}, idempotency="same-key")
        self.assertEqual(caught.exception.code, 409)

    def test_capture_api_returns_accepted_without_raw_material(self) -> None:
        with self.request("/v1/memory/capture", body={"material": "silent capture"}) as response:
            self.assertEqual(response.status, 202)
            payload = json.load(response)
        self.assertEqual(payload["status"], "queued")
        self.assertNotIn("material", payload)

    def test_conversation_observe_api_returns_receipt_and_status(self) -> None:
        body = {
            "conversation_id": "api-chat",
            "turn_id": "turn-1",
            "role": "user",
            "content": "API 对话事件先落本地再后台处理。",
            "client": "api-test",
        }
        with self.request("/v1/conversations/observe", body=body) as response:
            self.assertEqual(response.status, 202)
            observed = json.load(response)
        self.assertEqual(observed["status"], "queued_for_analysis")
        self.assertNotIn("content", observed)
        with self.request(f"/v1/conversations/status?event_id={observed['id']}") as response:
            status = json.load(response)
        self.assertEqual(status["id"], observed["id"])
        self.assertEqual(status["capture_status"], "queued")

    def test_conversation_observe_api_has_natural_turn_idempotency(self) -> None:
        body = {
            "conversation_id": "api-idempotent-chat",
            "turn_id": "turn-1",
            "role": "user",
            "content": "same turn",
        }
        with self.request("/v1/conversations/observe", body=body) as response:
            first = json.load(response)
        with self.request("/v1/conversations/observe", body=body) as response:
            replay = json.load(response)
        self.assertEqual(first["id"], replay["id"])
        self.assertTrue(replay["idempotent_replay"])
        changed = dict(body)
        changed["content"] = "changed turn"
        with self.assertRaises(HTTPError) as caught:
            self.request("/v1/conversations/observe", body=changed)
        self.assertEqual(caught.exception.code, 409)

    def test_person_api_is_disabled_until_explicit_setup(self) -> None:
        with self.request("/v1/person/health") as response:
            health = json.load(response)
        self.assertFalse(health["configured"])
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/v1/person/claims/propose",
                body={
                    "statement": "回答直接。",
                    "claim_type": "communication_preference",
                    "source_refs": ["api:test"],
                },
            )
        self.assertEqual(caught.exception.code, 503)

    def test_person_api_setup_claim_lifecycle_and_minimal_context(self) -> None:
        with self.request(
            "/v1/person/setup",
            body={"path": str(self.personal_root), "confirm": True},
        ) as response:
            setup = json.load(response)
        self.assertTrue(setup["ready"])
        self.assertNotIn(str(self.personal_root), json.dumps(setup))
        with self.request(
            "/v1/person/claims/propose",
            body={
                "statement": "回答先说结论。",
                "claim_type": "communication_preference",
                "source_refs": ["api:test:turn-1"],
            },
            idempotency="person-propose-once",
        ) as response:
            proposed = json.load(response)
        self.assertFalse(proposed["effective"])
        with self.request(
            "/v1/person/claims/confirm",
            body={"claim_id": proposed["id"]},
        ) as response:
            confirmed = json.load(response)
        self.assertTrue(confirmed["effective"])
        self.assertEqual(confirmed["access_scope"], ["personal-core"])
        with self.request(
            "/v1/person/context",
            body={"task": "准备回答", "agent": "codex", "token_budget": 256},
        ) as response:
            private_context = json.load(response)
        self.assertEqual(private_context["claims"], [])
        with self.request(
            "/v1/person/claims/authorize",
            body={"claim_id": proposed["id"], "access_scope": ["agent:codex"], "source_ref": "api:authorization"},
        ) as response:
            authorized = json.load(response)
        self.assertEqual(authorized["access_scope"], ["agent:codex"])
        with self.request(
            "/v1/person/context",
            body={"task": "准备回答", "agent": "codex", "token_budget": 256},
        ) as response:
            context = json.load(response)
        self.assertEqual(context["claims"][0]["claim_id"], proposed["id"])
        self.assertLessEqual(context["token_estimate"], 256)
        with self.request(f"/v1/person/claims/explain?id={proposed['id']}") as response:
            explanation = json.load(response)
        self.assertIn("api:test:turn-1", explanation["explanation"]["sources"])
        with self.request("/v1/health") as response:
            health = json.load(response)
        self.assertNotIn(str(self.personal_root), json.dumps(health))

    def test_agent_token_is_scoped_and_body_cannot_spoof_agent_identity(self) -> None:
        self.service.setup_personal_core(str(self.personal_root), confirm=True)
        proposed = self.service.propose_person_claim(
            statement="回答先说结论。",
            claim_type="communication_preference",
            source_refs=["api:agent-scope"],
        )
        self.service.confirm_person_claim(proposed["id"])
        self.service.authorize_person_claim(proposed["id"], access_scope=["agent:codex"])
        issued = self.service.issue_agent_credential("codex", scopes=["context:read"])
        with self.request(
            "/v1/person/context",
            body={"task": "准备回答", "agent": "spoofed-agent", "token_budget": 256},
            token=issued["token"],
        ) as response:
            context = json.load(response)
        self.assertEqual(context["claims"][0]["claim_id"], proposed["id"])
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/v1/conversations/observe",
                body={"conversation_id": "scoped", "turn_id": "1", "role": "user", "content": "test"},
                token=issued["token"],
            )
        self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(HTTPError) as caught:
            self.request("/v1/person/dashboard", token=issued["token"])
        self.assertEqual(caught.exception.code, 403)

    def test_person_dashboard_api_includes_learning_and_permission_layers(self) -> None:
        self.service.setup_personal_core(str(self.personal_root), confirm=True)
        self.service.issue_agent_credential("codex", scopes=["context:read"])
        self.service.observe_conversation(
            conversation_id="dashboard-chat",
            turn_id="1",
            role="user",
            content="我喜欢回答先看结论。",
            agent="codex",
            personal_signals=[{
                "candidate_statement": "用户偏好先看到结论，再阅读必要说明。",
                "claim_type": "communication_preference",
                "signal_kind": "explicit",
                "polarity": "support",
                "scope_kind": "global",
                "confidence": 0.98,
                "inference_basis": "用户明确表达了稳定的回答结构偏好",
                "concept_key": "communication.conclusion-first",
            }],
        )
        self.service.process_person_learning(limit=20)
        with self.request("/v1/person/dashboard?limit=50") as response:
            dashboard = json.load(response)
        self.assertTrue(dashboard["configured"])
        self.assertEqual(dashboard["permissions"]["active_count"], 1)
        self.assertEqual(dashboard["permissions"]["agents"][0]["agent_id"], "codex")
        self.assertTrue(dashboard["claims"]["understanding"])
        self.assertFalse(dashboard["claims"]["review_required"])

    def test_personal_backup_api_create_list_verify_and_restore(self) -> None:
        self.service.setup_personal_core(str(self.personal_root), confirm=True)
        self.service.propose_person_claim(
            statement="回答先说结论。",
            claim_type="communication_preference",
            source_refs=["api:backup"],
        )
        with self.request("/v1/person/backups/create", body={}, idempotency="personal-backup-create") as response:
            created = json.load(response)
        with self.request("/v1/person/backups?limit=10") as response:
            listed = json.load(response)
        self.assertEqual(listed["items"][0]["backup_id"], created["backup_id"])
        with self.request(
            "/v1/person/backups/verify",
            body={"backup_id": created["backup_id"]},
        ) as response:
            verified = json.load(response)
        self.assertTrue(verified["valid"])
        with self.request(
            "/v1/person/backups/restore",
            body={"backup_id": created["backup_id"], "confirm_personal_core": self.personal_root.name},
        ) as response:
            restored = json.load(response)
        self.assertTrue(restored["safety_backup"])

    def test_person_claim_forget_api_requires_confirmation_and_erases_claim(self) -> None:
        self.service.setup_personal_core(str(self.personal_root), confirm=True)
        statement = "用户要求个人记忆只保存抽象理解。"
        claim = self.service.propose_person_claim(
            statement=statement,
            claim_type="authority_rule",
            source_refs=["api:forget"],
        )
        confirm_key = f"confirm-{claim['id']}"
        with self.request(
            "/v1/person/claims/confirm",
            body={"claim_id": claim["id"], "source_ref": "api:confirmation"},
            idempotency=confirm_key,
        ):
            pass
        with self.assertRaises(HTTPError) as caught:
            self.request("/v1/person/claims/forget", body={"claim_id": claim["id"]})
        self.assertEqual(caught.exception.code, 403)
        with self.request(
            "/v1/person/claims/forget",
            body={"claim_id": claim["id"], "confirm_forget": True},
            idempotency="person-forget-once",
        ) as response:
            forgotten = json.load(response)
        self.assertTrue(forgotten["forgotten"])
        with self.assertRaises(NotFoundError):
            self.service.person_claim(claim["id"])
        idempotency_text = (self.vault / ".bok/state/idempotency.json").read_text(encoding="utf-8")
        self.assertNotIn(confirm_key, idempotency_text)
        self.assertNotIn(statement, idempotency_text)


class BokUIBridgeContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary.name).resolve()
        for name in ("00-System", "01-Inbox", "02-Projects", "03-Knowledge"):
            (self.vault / name).mkdir()
        (self.vault / "00-System/Active-Context.md").write_text(
            "focus_path: 02-Projects/p.md\n",
            encoding="utf-8",
        )
        (self.vault / "02-Projects/p.md").write_text(
            "# P\n\n## 下一步行动\n\n- Test\n",
            encoding="utf-8",
        )
        self.bridge = BokUIBridge(
            self.vault,
            config_overrides={
                "provider": "none",
                "embedding_provider": "none",
                "auto_start_local_model": False,
            },
        )

    def tearDown(self) -> None:
        self.bridge.close()
        self.temporary.cleanup()

    def test_bridge_authenticates_server_side_without_exposing_token(self) -> None:
        response = self.bridge.forward("GET", "/api/bok/v1/health")
        payload = json.loads(response.body)
        token = (self.vault / ".bok/auth-token").read_text(encoding="utf-8").strip()
        self.assertEqual(response.status, 200)
        self.assertTrue(payload["ready"])
        self.assertNotIn(token, response.body.decode("utf-8"))

    def test_bridge_preserves_idempotent_quick_note_creation(self) -> None:
        body = json.dumps({"text": "Only once", "source": "ui-test"}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Idempotency-Key": "ui-note-once"}
        first = self.bridge.forward("POST", "/api/bok/v1/quick-notes", body=body, headers=headers)
        second = self.bridge.forward("POST", "/api/bok/v1/quick-notes", body=body, headers=headers)
        self.assertEqual(first.status, 200)
        self.assertEqual(json.loads(first.body)["path"], json.loads(second.body)["path"])
        self.assertTrue(json.loads(second.body)["idempotent_replay"])
        self.assertEqual(len(list((self.vault / "07-Quick-Notes").glob("*.md"))), 1)

    def test_bridge_keeps_auth_rotation_out_of_browser_context(self) -> None:
        response = self.bridge.forward("POST", "/api/bok/v1/auth/rotate", body=b"{}")
        payload = json.loads(response.body)
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"]["code"], "browser_route_forbidden")
        self.assertFalse((self.vault / ".bok/auth-token").exists())

    def test_bridge_keeps_personal_core_path_setup_out_of_browser_context(self) -> None:
        response = self.bridge.forward(
            "POST",
            "/api/bok/v1/person/setup",
            body=json.dumps({"path": "/tmp/personal", "confirm": True}).encode("utf-8"),
        )
        payload = json.loads(response.body)
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"]["code"], "browser_route_forbidden")
        self.assertFalse((self.vault / ".bok/auth-token").exists())

    def test_bridge_fails_closed_for_unknown_future_routes(self) -> None:
        response = self.bridge.forward("POST", "/api/bok/v1/person/claims/new-destructive-action", body=b"{}")
        payload = json.loads(response.body)
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"]["code"], "browser_route_forbidden")


if __name__ == "__main__":
    unittest.main(verbosity=2)
