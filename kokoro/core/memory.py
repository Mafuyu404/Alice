"""Memory backend adapters."""

from __future__ import annotations

import datetime
import logging
import os
import re
import warnings
from pathlib import Path
from typing import Any

from . import config as cfg_mod
from . import prompts
from . import token_usage

logger = logging.getLogger("memory")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

warnings.filterwarnings(
    "ignore",
    message=r"Payload indexes have no effect in the local Qdrant\..*",
    category=UserWarning,
    module=r"mem0\.vector_stores\.qdrant",
)


class MemoryBackend:
    def get_context(self, query: str, user_id: str = "default") -> str:
        return ""

    def get_context_multi(self, query: str, user_ids: list[str]) -> str:
        seen: set[str] = set()
        parts: list[str] = []
        for user_id in user_ids:
            if not user_id or user_id in seen:
                continue
            seen.add(user_id)
            ctx = self.get_context(query, user_id=user_id)
            if ctx:
                parts.append(ctx.strip())
        return "\n".join(parts).strip()

    def store(self, user_msg: str, assistant_msg: str, user_id: str = "default", name: str = "助手") -> None:
        pass

    def list_memories(self, user_id: str = "default", limit: int = 200) -> list[dict[str, Any]]:
        return []

    def delete_all(self, user_id: str = "default") -> int:
        return 0

    def close(self) -> None:
        pass

    @property
    def ready(self) -> bool:
        return False


class KokoroMemoBackend(MemoryBackend):
    @property
    def ready(self) -> bool:
        return True


class NoMemoryBackend(MemoryBackend):
    pass


class Mem0Backend(MemoryBackend):
    def __init__(self, config: dict):
        self._mem = None
        self._ok = False
        self._store_count = 0

        lifecycle = config.get("mem0", {}).get("lifecycle", {})
        self._lc = {
            "importance_min_len": lifecycle.get("importance_min_len", 10),
            "importance_mode": lifecycle.get("importance_mode", "auto"),
            "importance_llm": lifecycle.get("importance_llm", ""),
            "importance_llm_url": lifecycle.get("importance_llm_url", ""),
            "search_threshold": lifecycle.get("search_threshold", 0.3),
            "search_top_k": lifecycle.get("search_top_k", 8),
            "compress_interval": lifecycle.get("compress_interval", 50),
            "max_memories_per_user": lifecycle.get("max_memories_per_user", 200),
        }
        self._init(config)

    @property
    def ready(self) -> bool:
        return self._ok

    @staticmethod
    def _embedder_config(embedder: dict) -> dict:
        provider = embedder.get("provider", "fastembed")
        if provider == "fastembed":
            return {
                "provider": "fastembed",
                "config": {
                    "model": embedder.get("model", "BAAI/bge-small-zh-v1.5"),
                    "embedding_dims": embedder.get("embedding_dims", 512),
                },
            }
        return {
            "provider": "ollama",
            "config": {
                "model": embedder.get("model", "bge-m3:latest"),
                "ollama_base_url": embedder.get("base_url", "http://127.0.0.1:11434"),
                "embedding_dims": embedder.get("embedding_dims", 1024),
            },
        }

    @staticmethod
    def _vector_store_path(embedder_cfg: dict) -> str:
        model = str(embedder_cfg.get("config", {}).get("model", "")).strip() or "default"
        dims = int(embedder_cfg.get("config", {}).get("embedding_dims", 0) or 0)
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", model.replace(":", "_")).strip("._-") or "default"
        base_dir = _PROJECT_ROOT / "mem0_data"
        return str(base_dir / f"{slug}_{dims}d")

    @staticmethod
    def _history_db_path(embedder_cfg: dict) -> str:
        base_dir = Mem0Backend._vector_store_path(embedder_cfg)
        return os.path.join(base_dir, "history.db")

    @staticmethod
    def _collection_name(embedder_cfg: dict) -> str:
        model = str(embedder_cfg.get("config", {}).get("model", "")).strip() or "default"
        dims = int(embedder_cfg.get("config", {}).get("embedding_dims", 0) or 0)
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", model.replace(":", "_")).strip("._-") or "default"
        return f"mem0_{slug}_{dims}d"

    def _init(self, config: dict) -> None:
        mem_cfg = config.get("mem0", {})
        llm = mem_cfg.get("llm", {})
        embedder = mem_cfg.get("embedder", {})
        embedder_cfg = self._embedder_config(embedder)
        llm_model = llm.get("model", "qwen2.5:1.5b")

        try:
            os.environ.setdefault("MEM0_TELEMETRY", "False")
            from mem0 import Memory
            from mem0.vector_stores.qdrant import Qdrant as Mem0Qdrant
            from qdrant_client.models import Distance, VectorParams

            if not getattr(Mem0Qdrant, "_alice_bm25_disabled", False):
                def _create_col_without_bm25(self, vector_size: int, on_disk: bool, distance: Distance = Distance.COSINE):
                    response = self.list_cols()
                    for collection in response.collections:
                        if collection.name == self.collection_name:
                            logger.debug(f"Collection {self.collection_name} already exists. Skipping creation.")
                            self._has_bm25_slot = False
                            self._create_filter_indexes()
                            return
                    self.client.create_collection(
                        collection_name=self.collection_name,
                        vectors_config=VectorParams(size=vector_size, distance=distance, on_disk=on_disk),
                    )
                    self._has_bm25_slot = False
                    self._create_filter_indexes()

                Mem0Qdrant.create_col = _create_col_without_bm25
                Mem0Qdrant._alice_bm25_disabled = True

            runtime_config = {
                "llm": {
                    "provider": llm.get("provider", "ollama"),
                    "config": {
                        "model": llm_model,
                        "ollama_base_url": llm.get("base_url", "http://127.0.0.1:11434"),
                        "temperature": 0.7,
                        "max_tokens": 512,
                        "http_client_proxies": None,
                    },
                },
                "embedder": embedder_cfg,
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "path": self._vector_store_path(embedder_cfg),
                        "collection_name": self._collection_name(embedder_cfg),
                        "embedding_model_dims": embedder_cfg["config"]["embedding_dims"],
                    },
                },
                "history_db_path": self._history_db_path(embedder_cfg),
                "version": "v1.1",
            }
            self._mem = Memory.from_config(runtime_config)
            self._ok = True
            logger.info("mem0 ready (llm=%s, embedder=%s)", llm_model, embedder_cfg["provider"])
        except ImportError:
            logger.warning("mem0 not installed; run `pip install mem0ai`")
        except Exception as exc:
            logger.warning("mem0 init failed: %s", exc)

    @staticmethod
    def _format_time(created_at: str | None) -> str:
        if not created_at:
            return ""
        try:
            dt = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            now = datetime.datetime.now(dt.tzinfo)
            diff = now - dt
            if diff.days == 0:
                return "今天"
            if diff.days == 1:
                return "昨天"
            if diff.days < 7:
                return f"{diff.days}天前"
            return dt.strftime("%m-%d")
        except (ValueError, TypeError):
            return ""

    def get_context(self, query: str, user_id: str = "default") -> str:
        if not self._ok:
            return ""
        try:
            result = self._mem.search(
                query=query,
                filters={"user_id": user_id},
                threshold=self._lc["search_threshold"],
                top_k=self._lc["search_top_k"],
            )
            items = result.get("results") or []
            items.sort(key=lambda item: item.get("score", 0) or 0, reverse=True)

            # Dedup: skip items too similar to earlier (higher-scored) ones
            seen: list[str] = []
            lines = []
            for item in items:
                text = item.get("memory", "")
                if not text:
                    continue
                # Simple dedup by normalized text overlap
                norm = re.sub(r"[^一-鿟\w]", "", text).lower()[:80]
                if norm and len(norm) >= 4:
                    is_dup = False
                    for existing in seen:
                        if norm in existing or existing in norm:
                            is_dup = True
                            break
                    if is_dup:
                        continue
                    seen.append(norm)

                tag = self._format_time(item.get("created_at"))
                meta_tags = []
                metadata = item.get("metadata") or {}
                raw_tags = metadata.get("tags") if isinstance(metadata, dict) else None
                if raw_tags and isinstance(raw_tags, list):
                    meta_tags = [str(t) for t in raw_tags if t]
                tag_str = f"[{tag}]" if tag else ""
                tags_str = f" #{' #'.join(meta_tags)}" if meta_tags else ""
                line = f"- {tag_str}{tags_str} {text}" if (tag_str or tags_str) else f"- {text}"
                lines.append(line)
            return "\n\n【记忆】\n" + "\n".join(lines) if lines else ""
        except Exception as exc:
            logger.warning("mem0 search failed: %s", exc)
            return ""

    def _is_trivial(self, text: str) -> bool:
        stripped = text.strip()
        if len(stripped) < self._lc["importance_min_len"]:
            return True
        return stripped.lower() in {"嗯", "好", "ok", "好的", "是的", "对", "是", "?", "？", "。", "..."}

    def _check_importance_llm(self, user_msg: str, assistant_msg: str, name: str = "助手") -> bool:
        model = self._lc["importance_llm"] or "qwen2.5:1.5b"
        prompt = prompts.format_prompt(
            "memory_importance.user_template",
            user_msg=user_msg,
            assistant_msg=assistant_msg,
            user_name=cfg_mod.user_name(),
            name=name,
        )
        try:
            from kokoro.core import deepseek_api

            result = deepseek_api.chat(
                [{"role": "user", "content": prompt}],
                model=model,
                temperature=0.1,
                max_tokens=16,
                function="memory_importance",
                timeout=15,
            )
            return "不重要" in result["content"]
        except Exception as exc:
            logger.warning("[mem0] importance LLM call failed: %s", exc)
        return self._is_trivial(user_msg) and self._is_trivial(assistant_msg)

    def _is_conversation_trivial(self, user_msg: str, assistant_msg: str, name: str = "助手") -> bool:
        if self._lc["importance_mode"] == "auto":
            return self._check_importance_llm(user_msg, assistant_msg, name=name)
        return self._is_trivial(user_msg) and self._is_trivial(assistant_msg)

    def store(self, user_msg: str, assistant_msg: str, user_id: str = "default", name: str = "助手") -> None:
        if not self._ok:
            return
        try:
            if self._is_conversation_trivial(user_msg, assistant_msg, name=name):
                logger.debug("[mem0] skipped trivial conversation")
                return

            self._mem.add(
                [
                    {"role": "user", "content": user_msg},
                    {"role": "assistant", "content": assistant_msg},
                ],
                user_id=user_id,
            )

            self._store_count += 1
            if self._store_count >= self._lc["compress_interval"]:
                self._store_count = 0
                self._cleanup(user_id)
        except Exception as exc:
            logger.warning("mem0 store failed: %s", exc)

    def list_memories(self, user_id: str = "default", limit: int = 200) -> list[dict[str, Any]]:
        if not self._ok:
            return []
        try:
            result = self._mem.get_all(filters={"user_id": user_id}, top_k=max(1, limit))
            items = result.get("results") or []
        except Exception as exc:
            logger.warning("mem0 list failed: %s", exc)
            return []

        normalized: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "id": item.get("id", ""),
                    "memory": item.get("memory", ""),
                    "created_at": item.get("created_at", ""),
                    "updated_at": item.get("updated_at", ""),
                    "score": item.get("score"),
                    "metadata": item.get("metadata") or {},
                }
            )
        normalized.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return normalized

    def close(self) -> None:
        if self._mem is None:
            return
        self._close_qdrant_clients()
        try:
            close = getattr(self._mem, "close", None)
            if callable(close):
                close()
        except Exception as exc:
            logger.debug("mem0 close failed: %s", exc)
        finally:
            self._mem = None

    def _close_qdrant_clients(self) -> None:
        """Close local Qdrant clients before interpreter teardown.

        qdrant-client's ``__del__`` calls ``close()`` again during shutdown. On
        Windows that can happen after ``msvcrt`` has been unloaded, producing a
        noisy exception and occasionally leaving local lock files behind.
        """
        for attr in ("vector_store", "entity_store"):
            store = getattr(self._mem, attr, None)
            client = getattr(store, "client", None)
            if client is None:
                continue
            try:
                client.close()
            except Exception as exc:
                logger.debug("qdrant client close failed for %s: %s", attr, exc)
            try:
                if hasattr(client, "_client"):
                    delattr(client, "_client")
            except Exception:
                pass
            try:
                setattr(store, "client", None)
            except Exception:
                pass

    def delete_all(self, user_id: str = "default") -> int:
        if not self._ok:
            return 0
        deleted = 0
        try:
            result = self._mem.get_all(filters={"user_id": user_id}, top_k=10000)
            for item in result.get("results", []) or []:
                memory_id = item.get("id")
                if not memory_id:
                    continue
                self._mem.delete(memory_id)
                deleted += 1
        except Exception as exc:
            logger.warning("mem0 delete_all failed for %s: %s", user_id, exc)
        return deleted

    def _cleanup(self, user_id: str) -> None:
        limit = self._lc["max_memories_per_user"]
        try:
            result = self._mem.get_all(filters={"user_id": user_id}, top_k=limit * 5)
            all_memories = result.get("results", [])
            if len(all_memories) <= limit:
                return
            all_memories.sort(key=lambda item: item.get("created_at", "") or "")
            for item in all_memories[: len(all_memories) - limit]:
                self._mem.delete(item["id"])
        except Exception as exc:
            logger.warning("[mem0] cleanup failed: %s", exc)


def normalize_entity_label(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return "unknown"
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", text)
    text = text.strip("_").lower()
    return text or "unknown"


def scoped_user_id(owner_id: str, counterpart: str | None = None) -> str:
    """Return the single memory namespace for a character.

    Memory belongs to the character, not to a counterpart/session dimension.
    The counterpart argument is kept for compatibility with older callers.
    """
    return normalize_entity_label(owner_id)


def context_user_ids(owner_id: str, counterpart: str | None = None) -> list[str]:
    return [scoped_user_id(owner_id)]


def create_backend(config: dict) -> MemoryBackend:
    backend = config.get("memory_backend", "mem0")
    if backend == "mem0":
        return Mem0Backend(config)
    if backend == "none":
        return NoMemoryBackend()
    return KokoroMemoBackend()
