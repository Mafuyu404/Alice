"""Memory backend adapters."""

from __future__ import annotations

import datetime
import logging
import os

from . import config as cfg_mod
from . import prompts

logger = logging.getLogger("memory")


class MemoryBackend:
    def get_context(self, query: str, user_id: str = "default") -> str:
        return ""

    def store(self, user_msg: str, assistant_msg: str, user_id: str = "default") -> None:
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
                "model": embedder.get("model", "qwen2.5:0.5b"),
                "ollama_base_url": embedder.get("base_url", "http://127.0.0.1:11434"),
                "embedding_dims": embedder.get("embedding_dims", 896),
            },
        }

    def _init(self, config: dict) -> None:
        mem_cfg = config.get("mem0", {})
        llm = mem_cfg.get("llm", {})
        embedder = mem_cfg.get("embedder", {})
        embedder_cfg = self._embedder_config(embedder)
        llm_model = llm.get("model", "qwen2.5:1.5b")

        try:
            from mem0 import Memory

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
                        "path": os.path.join(
                            os.path.dirname(os.path.dirname(__file__)),
                            "mem0_data",
                        ),
                        "embedding_model_dims": embedder_cfg["config"]["embedding_dims"],
                    },
                },
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

            lines = []
            for item in items:
                text = item.get("memory", "")
                if not text:
                    continue
                tag = self._format_time(item.get("created_at"))
                lines.append(f"- [{tag}] {text}" if tag else f"- {text}")
            return "\n\n【记忆】\n" + "\n".join(lines) if lines else ""
        except Exception as exc:
            logger.warning("mem0 search failed: %s", exc)
            return ""

    def _is_trivial(self, text: str) -> bool:
        stripped = text.strip()
        if len(stripped) < self._lc["importance_min_len"]:
            return True
        return stripped.lower() in {"嗯", "好", "ok", "好的", "是的", "对", "是", "?", "？", "。", "..."}

    def _check_importance_llm(self, user_msg: str, assistant_msg: str) -> bool:
        model = self._lc["importance_llm"] or "qwen2.5:1.5b"
        base_url = self._lc["importance_llm_url"] or cfg_mod.llm_url()
        prompt = prompts.format_prompt(
            "memory_importance.user_template",
            user_msg=user_msg,
            assistant_msg=assistant_msg,
        )
        try:
            import requests

            resp = requests.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 16,
                },
                timeout=15,
            )
            if resp.ok:
                text = resp.json()["choices"][0]["message"]["content"].strip()
                return "不重要" in text
            logger.warning("[mem0] importance LLM returned %s", resp.status_code)
        except Exception as exc:
            logger.warning("[mem0] importance LLM call failed: %s", exc)
        return self._is_trivial(user_msg) and self._is_trivial(assistant_msg)

    def _is_conversation_trivial(self, user_msg: str, assistant_msg: str) -> bool:
        if self._lc["importance_mode"] == "auto":
            return self._check_importance_llm(user_msg, assistant_msg)
        return self._is_trivial(user_msg) and self._is_trivial(assistant_msg)

    def store(self, user_msg: str, assistant_msg: str, user_id: str = "default") -> None:
        if not self._ok:
            return
        try:
            if self._is_conversation_trivial(user_msg, assistant_msg):
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


def create_backend(config: dict) -> MemoryBackend:
    backend = config.get("memory_backend", "mem0")
    if backend == "mem0":
        return Mem0Backend(config)
    if backend == "none":
        return NoMemoryBackend()
    return KokoroMemoBackend()
