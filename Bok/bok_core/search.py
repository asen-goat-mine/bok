from __future__ import annotations

import math
import os
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .config import BokConfig
from .errors import BokError
from .markdown import MarkdownChunk, MarkdownDocument, chunk_document, parse_document, terms
from .provider import ProviderClient
from .storage import VaultStorage
from .util import atomic_write_json, estimate_tokens, read_json, sha256_text, truncate_to_token_budget


@dataclass
class IndexedChunk:
    chunk: MarkdownChunk
    term_counts: Counter
    term_count: int
    modified: float


@dataclass
class SearchIndexState:
    fingerprint: str = ""
    documents: Dict[str, MarkdownDocument] = field(default_factory=dict)
    chunks: List[IndexedChunk] = field(default_factory=list)
    document_frequency: Counter = field(default_factory=Counter)
    catalog_documents: int = 0


class VaultSearch:
    """Paragraph-level lexical retrieval with optional provider embeddings."""

    def __init__(self, config: BokConfig, storage: VaultStorage):
        self.config = config
        self.storage = storage
        self.lock = threading.RLock()
        self.indexes: Dict[str, SearchIndexState] = {
            "default": SearchIndexState(),
            "all": SearchIndexState(),
        }
        self.provider = ProviderClient(config)
        self.embedding_cache_path = config.state_dir / "cache" / "embeddings.json"

    @staticmethod
    def _expanded_terms(text: str) -> List[str]:
        result = terms(text)
        cjk_runs = re.findall(r"[\u3400-\u9fff]{2,}", text)
        for run in cjk_runs:
            result.extend(run[index:index + 2] for index in range(len(run) - 1))
        return list(dict.fromkeys(item for item in result if item))

    @staticmethod
    def _matches_prefix(relative: str, prefix: str) -> bool:
        value = relative.casefold().strip("/")
        target = prefix.replace("\\", "/").casefold().strip("/")
        return bool(target) and (value == target or value.startswith(target + "/"))

    def _is_deferred(self, relative: str) -> bool:
        return any(self._matches_prefix(relative, prefix) for prefix in self.config.deferred_search_prefixes)

    def _effective_scope(self, scope: str, path_prefix: str = "") -> str:
        requested = str(scope or "default").casefold()
        if requested not in {"default", "all"}:
            raise BokError("invalid_search_scope", "Search scope must be default or all")
        if requested == "all" or (path_prefix and self._is_deferred(path_prefix)):
            return "all"
        return "default"

    def _source_fingerprint(self, scope: str) -> Tuple[str, List[Tuple[Path, os.stat_result]], int]:
        snapshots = []
        parts = []
        catalog_documents = 0
        for path in self.storage.markdown_files():
            try:
                stat = path.stat()
            except OSError:
                continue
            relative = self.storage.relative(path)
            catalog_documents += 1
            if scope == "default" and self._is_deferred(relative):
                continue
            snapshots.append((path, stat))
            parts.append(f"{relative}:{stat.st_mtime_ns}:{stat.st_size}")
        return "|".join(parts), snapshots, catalog_documents

    def refresh(self, force: bool = False, *, scope: str = "default") -> dict:
        scope = self._effective_scope(scope)
        source, snapshots, catalog_documents = self._source_fingerprint(scope)
        state = self.indexes[scope]
        with self.lock:
            if not force and source == state.fingerprint and state.chunks:
                return {
                    "changed": False,
                    "scope": scope,
                    "documents": len(state.documents),
                    "chunks": len(state.chunks),
                    "catalog_documents": state.catalog_documents,
                    "deferred_documents": max(0, state.catalog_documents - len(state.documents)),
                }
        documents: Dict[str, MarkdownDocument] = {}
        chunks: List[IndexedChunk] = []
        frequency: Counter = Counter()
        for path, stat in snapshots:
            relative = self.storage.relative(path)
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            document = parse_document(path, relative, text)
            documents[relative] = document
            for chunk in chunk_document(document):
                expanded = self._expanded_terms(" ".join([chunk.title, chunk.heading, " ".join(chunk.tags), " ".join(chunk.aliases), chunk.text]))
                counts = Counter(expanded)
                chunks.append(IndexedChunk(chunk, counts, sum(counts.values()), stat.st_mtime))
                frequency.update(set(counts))
        with self.lock:
            state.fingerprint = source
            state.documents = documents
            state.chunks = chunks
            state.document_frequency = frequency
            state.catalog_documents = catalog_documents
        return {
            "changed": True,
            "scope": scope,
            "documents": len(documents),
            "chunks": len(chunks),
            "catalog_documents": catalog_documents,
            "deferred_documents": max(0, catalog_documents - len(documents)),
        }

    def invalidate(self) -> None:
        """Mark the derived index stale without delaying the user's write path."""
        with self.lock:
            for state in self.indexes.values():
                state.fingerprint = ""

    def focus_path(self) -> str:
        # Prefer the already validated Markdown index without forcing another
        # Vault scan. This also avoids a Windows temporary-path edge case when
        # the same file was successfully indexed moments earlier.
        with self.lock:
            document = self.indexes["default"].documents.get("00-System/Active-Context.md")
        if document:
            configured = str(document.frontmatter.get("focus_path") or "").strip()
            if configured:
                return configured.replace("\\", "/")
            match = re.search(r"(?m)^focus_path:\s*([^\r\n]+)$", document.text)
            if match:
                return match.group(1).strip().replace("\\", "/")
        try:
            text = self.storage.read_text("00-System/Active-Context.md")
        except (BokError, OSError):
            return ""
        match = re.search(r"(?m)^focus_path:\s*([^\r\n]+)$", text)
        return match.group(1).strip().replace("\\", "/") if match else ""

    @staticmethod
    def _resume_intent(query: str) -> bool:
        return bool(re.search(r"(?:接着|继续|上次|当前项目|这个项目|做到哪|下一步|进度|收尾|续接)", query))

    @staticmethod
    def _generic_resume_intent(query: str) -> bool:
        """Route only subject-free continuation requests to the current project."""
        normalized = re.sub(r"[\s，。！？、,.!?：:；;]+", "", query.casefold())
        normalized = re.sub(r"^(?:(?:请问|请|帮我|麻烦|我们|咱们|现在|目前|先|再)+)", "", normalized)
        normalized = re.sub(r"(?:一下|看看|看下|吧|呢|吗|了)+$", "", normalized)
        return bool(re.fullmatch(
            r"(?:接着|继续)(?:(?:上次|之前|当前|这个)(?:的)?(?:项目|工作|任务)?)?(?:做|进行|推进|开发|制作)?"
            r"|(?:当前|这个)?(?:项目|工作|任务)?(?:做到哪|进度(?:如何|怎么样|到哪)?|下一步(?:行动|做什么|该做什么)?)"
            r"|(?:收尾|续接)(?:当前|这个)?(?:项目|工作|任务)?",
            normalized,
        ))

    @staticmethod
    def _resume_section_key(item: IndexedChunk, query: str) -> tuple:
        """Prefer actionable sections and dated updates over old project prose."""
        heading = item.chunk.heading.casefold()
        next_action = bool(re.search(r"下一步|后续行动|next(?:\s+steps?|\s+actions?)?$", heading))
        current_status = bool(re.search(r"当前状态|当前进度|最新进展|current\s+status", heading))
        dates = re.findall(r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)", heading)
        latest_date = max((tuple(map(int, date)) for date in dates), default=(0, 0, 0))
        action_query = bool(re.search(r"接着|继续|下一步|收尾|续接", query))
        if next_action:
            priority = 0 if action_query else 2
        elif dates:
            priority = 1 if action_query else 0
        elif current_status:
            priority = 2 if action_query else 1
        elif re.search(r"一句话结论|summary", heading):
            priority = 3
        else:
            priority = 4
        return priority, tuple(-part for part in latest_date), item.chunk.ordinal

    @staticmethod
    def _locator_intent(query: str) -> bool:
        return bool(re.search(r"(?:哪里|在哪|位置|路径|文件|成片|终版|预览|打开|找到)", query))

    @staticmethod
    def _style_intent(query: str) -> bool:
        return bool(re.search(r"(?:按我|我的|平时|一贯).{0,8}(?:风格|习惯|口吻|写法)|(?:风格|口吻).{0,5}(?:写|改|生成)", query))

    @staticmethod
    def _explicit_document_query(query: str, chunk: MarkdownChunk) -> bool:
        normalized = query.casefold().strip()
        title = chunk.title.casefold().strip()
        path = chunk.path.casefold().strip()
        stem = Path(chunk.path).stem.casefold().replace("-", " ")
        return bool(
            normalized
            and (
                normalized == title
                or title in normalized
                or path in normalized
                or (len(stem) >= 4 and stem in normalized.replace("-", " "))
            )
        )

    def _source_adjustment(self, item: IndexedChunk, query: str, *, matched: int, coverage: float) -> Tuple[float, List[str]]:
        chunk = item.chunk
        explicit = self._explicit_document_query(query, chunk)
        if chunk.status in {"deprecated", "archived"} and not explicit:
            return -5.0, ["inactive_source_downrank"]
        if chunk.document_type == "skill" and not explicit:
            return -45.0, ["skill_source_downrank"]
        if chunk.document_role == "asset-index":
            if self._locator_intent(query) and matched > 0:
                subject_terms = [
                    term for term in self._expanded_terms(query)
                    if term not in {"哪里", "在哪", "位置", "路径", "文件", "成片", "终版", "预览", "打开", "找到"}
                ]
                heading_bonus = 12.0 if any(term in chunk.heading.casefold() for term in subject_terms) else 0.0
                return 18.0 + heading_bonus, ["asset_locator_authority"]
            if not explicit:
                return -12.0, ["asset_index_downrank"]
        if chunk.document_role == "navigation-index" and not explicit:
            return -25.0, ["navigation_index_downrank"]
        if matched <= 0 and not explicit:
            return 0.0, []
        if chunk.document_role == "knowledge-card":
            return 4.0, ["knowledge_card_authority"]
        if chunk.document_role == "style-guide" and self._style_intent(query):
            return 30.0, ["style_guide_authority"]
        if chunk.document_role == "project-brief" and coverage >= 0.25:
            return 1.5, ["project_card_authority"]
        if chunk.document_role in {"workflow", "style-guide"} and coverage >= 0.25:
            return 1.0, ["workflow_authority"]
        return 0.0, []

    def _lexical_score(self, item: IndexedChunk, query: str, query_terms: Sequence[str], focus: str, state: SearchIndexState) -> Tuple[float, List[str]]:
        chunk = item.chunk
        title = chunk.title.casefold()
        heading = chunk.heading.casefold()
        body = chunk.text.casefold()
        aliases = " ".join(chunk.aliases).casefold()
        tags = " ".join(chunk.tags).casefold()
        path = chunk.path.casefold()
        normalized = query.casefold().strip()
        score = 0.0
        reasons = []
        if normalized:
            if normalized == title or normalized in aliases.split("\n"):
                score += 16.0
                reasons.append("title_or_alias_exact")
            elif normalized in title:
                score += 10.0
                reasons.append("title_match")
            if normalized in heading:
                score += 7.0
                reasons.append("heading_match")
            if normalized in tags:
                score += 6.0
                reasons.append("tag_match")
            if normalized in body:
                score += 3.0
                reasons.append("phrase_match")
        total_chunks = max(1, len(state.chunks))
        matched = 0
        for term in query_terms:
            count = item.term_counts.get(term, 0)
            substring = term in body or term in title or term in heading or term in aliases or term in tags or term in path
            if count or substring:
                matched += 1
                frequency = state.document_frequency.get(term, 0)
                idf = math.log(1 + (total_chunks + 1) / (frequency + 1))
                score += idf * (1.0 + min(3, count))
                if term in title:
                    score += 2.5
                if term in heading:
                    score += 1.8
                if term in aliases:
                    score += 2.0
                if term in tags:
                    score += 1.5
        coverage = matched / len(query_terms) if query_terms else 0.0
        if query_terms:
            score *= 0.5 + coverage
            if coverage >= 0.999:
                reasons.append("all_terms")
        adjustment, authority_reasons = self._source_adjustment(item, normalized, matched=matched, coverage=coverage)
        score += adjustment
        reasons.extend(authority_reasons)
        if focus and chunk.path == focus:
            if self._resume_intent(normalized) and (self._generic_resume_intent(normalized) or (matched and coverage >= 0.35)):
                score += 30.0
                reasons.append("current_project_resume")
            elif matched and coverage >= 0.35:
                score += 3.0
                reasons.append("current_project")
        elif focus and matched and coverage >= 0.5 and chunk.path.rsplit("/", 1)[0] == focus.rsplit("/", 1)[0]:
            score += 0.75
            reasons.append("current_project_area")
        return score, reasons

    @staticmethod
    def _snippet(text: str, query_terms: Sequence[str], max_chars: int = 700) -> str:
        if len(text) <= max_chars:
            return text.strip()
        lower = text.casefold()
        positions = [lower.find(term) for term in query_terms if lower.find(term) >= 0]
        center = min(positions) if positions else 0
        start = max(0, center - max_chars // 3)
        end = min(len(text), start + max_chars)
        prefix = "…" if start else ""
        suffix = "…" if end < len(text) else ""
        return prefix + text[start:end].strip() + suffix

    @staticmethod
    def _cosine(left: List[float], right: List[float]) -> float:
        if not left or len(left) != len(right):
            return 0.0
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return max(-1.0, min(1.0, numerator / (left_norm * right_norm)))

    def _semantic_rerank(self, query: str, scored, eligible_chunks: Sequence[IndexedChunk], *, explicit_cloud_consent: bool) -> Tuple[list, dict]:
        if self.config.embedding_provider in ("", "none") or not self.config.embedding_model:
            return scored, {"status": "disabled"}
        full_semantic = self.provider.embedding_is_local() and len(eligible_chunks) <= self.config.semantic_full_scan_limit
        lexical = {item.chunk.chunk_id: (score, reasons) for score, item, reasons in scored}
        candidates = (
            [(lexical.get(item.chunk.chunk_id, (0.0, []))[0], item, lexical.get(item.chunk.chunk_id, (0.0, []))[1]) for item in eligible_chunks]
            if full_semantic
            else scored[:24]
        )
        if not candidates:
            return scored, {"status": "no_candidates"}
        cache = read_json(self.embedding_cache_path, {})
        if not isinstance(cache, dict):
            cache = {}
        model_key = f"{self.config.embedding_provider}:{self.config.embedding_model}"
        texts = [query] + ["\n".join([item.chunk.title, item.chunk.heading, item.chunk.text]) for _score, item, _reasons in candidates]
        keys = [sha256_text(model_key + "\n" + text) for text in texts]
        vectors = [cache.get(key) for key in keys]
        missing_indices = [index for index, vector in enumerate(vectors) if not isinstance(vector, list)]
        try:
            for offset in range(0, len(missing_indices), self.config.embedding_batch_size):
                batch_indices = missing_indices[offset:offset + self.config.embedding_batch_size]
                generated = self.provider.embed([texts[index] for index in batch_indices], explicit_cloud_consent=explicit_cloud_consent)
                for index, vector in zip(batch_indices, generated):
                    vectors[index] = vector
                    cache[keys[index]] = vector
            if missing_indices:
                self.storage.ensure_state()
                if len(cache) > 20000:
                    cache = dict(list(cache.items())[-20000:])
                atomic_write_json(self.embedding_cache_path, cache)
        except Exception as error:
            code = error.code if hasattr(error, "code") else type(error).__name__
            return scored, {"status": "degraded", "reason": code}
        query_vector = vectors[0]
        maximum = max((value[0] for value in candidates), default=1.0) or 1.0
        reranked = []
        lexical_weight, semantic_weight = (0.35, 0.65) if full_semantic else (0.72, 0.28)
        for index, (lexical, item, reasons) in enumerate(candidates, 1):
            semantic = (self._cosine(query_vector, vectors[index]) + 1.0) / 2.0
            if lexical <= 0 and semantic < 0.62:
                continue
            combined = 20.0 * (lexical_weight * min(1.0, lexical / maximum) + semantic_weight * semantic)
            updated_reasons = list(reasons)
            if semantic >= 0.72:
                updated_reasons.append("semantic_match")
            reranked.append((combined, item, updated_reasons))
        reranked.sort(key=lambda value: (-value[0], value[1].chunk.path, value[1].chunk.ordinal))
        if not full_semantic:
            reranked.extend(scored[24:])
        return reranked, {
            "status": "applied",
            "mode": "full_local_retrieval" if full_semantic else "minimal_cloud_rerank",
            "provider": self.config.embedding_provider,
            "model": self.config.embedding_model,
            "candidate_count": len(candidates),
        }

    def search(self, query: str, *, limit: Optional[int] = None, token_budget: Optional[int] = None, path_prefix: str = "", tags: Optional[List[str]] = None, semantic: bool = True, explicit_cloud_consent: bool = False, scope: str = "default") -> dict:
        scope = self._effective_scope(scope, path_prefix)
        self.refresh(scope=scope)
        state = self.indexes[scope]
        query = str(query or "").strip()
        if not query:
            return {"query": query, "results": [], "token_estimate": 0, "retrieval": "hybrid-local", "scope": scope}
        query_terms = self._expanded_terms(query)
        focus = self.focus_path()
        requested_tags = {item.casefold() for item in (tags or []) if item}
        scored = []
        with self.lock:
            eligible_chunks = []
            for item in state.chunks:
                if path_prefix and not item.chunk.path.startswith(path_prefix.rstrip("/") + "/") and item.chunk.path != path_prefix.rstrip("/"):
                    continue
                if requested_tags and not requested_tags.intersection(tag.casefold() for tag in item.chunk.tags):
                    continue
                eligible_chunks.append(item)
            resume_route = bool(focus and self._generic_resume_intent(query) and any(item.chunk.path == focus for item in eligible_chunks))
            if resume_route:
                eligible_chunks = [item for item in eligible_chunks if item.chunk.path == focus]
            for item in eligible_chunks:
                score, reasons = self._lexical_score(item, query, query_terms, focus, state)
                if resume_route:
                    score = max(1.0, score)
                    reasons.append("current_project_route")
                if score > 0:
                    scored.append((score, item, reasons))
        scored.sort(key=lambda value: (-value[0], value[1].chunk.path, value[1].chunk.ordinal))
        semantic_status = {"status": "not_requested"}
        if semantic:
            scored, semantic_status = self._semantic_rerank(query, scored, eligible_chunks, explicit_cloud_consent=explicit_cloud_consent)
        if resume_route:
            scored.sort(key=lambda value: self._resume_section_key(value[1], query))
        try:
            limit_value = int(limit) if limit is not None else self.config.max_search_results
        except (TypeError, ValueError):
            limit_value = self.config.max_search_results
        try:
            budget_value = int(token_budget) if token_budget is not None else self.config.max_context_tokens
        except (TypeError, ValueError):
            budget_value = self.config.max_context_tokens
        limit = max(1, min(limit_value, 20))
        budget = max(128, min(budget_value, self.config.max_context_tokens))
        results = []
        used_tokens = 0
        seen_sections = set()
        path_counts: Counter = Counter()
        for allow_same_document_overflow in (False, True):
            for score, item, reasons in scored:
                chunk = item.chunk
                section_key = (chunk.path, chunk.heading)
                if section_key in seen_sections:
                    continue
                if not allow_same_document_overflow and path_counts[chunk.path] >= 2:
                    continue
                snippet = self._snippet(chunk.text, query_terms)
                remaining = budget - used_tokens
                if remaining <= 0:
                    break
                snippet = truncate_to_token_budget(snippet, remaining)
                tokens = estimate_tokens(snippet)
                if not snippet or tokens <= 0:
                    continue
                results.append({
                    "source_id": chunk.chunk_id,
                    "path": chunk.path,
                    "title": chunk.title,
                    "heading": chunk.heading,
                    "snippet": snippet,
                    "score": round(score, 4),
                    "why": reasons or ["term_match"],
                    "token_estimate": tokens,
                    "tags": chunk.tags,
                    "type": chunk.document_type,
                    "role": chunk.document_role,
                    "status": chunk.status,
                    "source": chunk.source,
                    "updated": chunk.updated,
                })
                seen_sections.add(section_key)
                path_counts[chunk.path] += 1
                used_tokens += tokens
                if len(results) >= limit:
                    break
            if len(results) >= limit or used_tokens >= budget:
                break
        return {
            "query": query,
            "results": results,
            "token_estimate": used_tokens,
            "token_budget": budget,
            "retrieval": "paragraph-hybrid" if semantic_status.get("status") == "applied" else "paragraph-lexical-structural",
            "semantic": semantic_status,
            "focus_path": focus,
            "scope": scope,
        }

    def context(self, task: str, **kwargs) -> dict:
        result = self.search(task, **kwargs)
        blocks = []
        for index, item in enumerate(result["results"], 1):
            blocks.append(f"[S{index}] {item['path']} — {item['heading']}\n{item['snippet']}")
            item["citation"] = f"S{index}"
        result["context"] = "\n\n".join(blocks)
        result["sources"] = [
            {key: item[key] for key in ("citation", "source_id", "path", "title", "heading", "why", "type", "role", "status", "source", "updated")}
            for item in result["results"]
        ]
        return result

    def document(self, relative: str) -> Optional[MarkdownDocument]:
        scope = self._effective_scope("default", relative)
        self.refresh(scope=scope)
        return self.indexes[scope].documents.get(relative)
