"""Impulse — LLM-based active speech planner.

Replaces the desire-accrual scheduler with an LLM-based approach:
1. After each conversation turn, capture screen + fetch related memories
2. Call planning LLM to produce/update a plan table
3. Execute plan items: idle-wait -> generate impulse speech -> re-plan
4. User interruption cancels all pending plan items
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

from kokoro import edge_cache
from kokoro import prompts as _prompts
from kokoro import screen_interest

logger = logging.getLogger(__name__)
DEFAULT_PLANNING_MODEL = "deepseek-v4-flash"
_META_TOPIC_RE = re.compile(r"(系统|主动搭话|触发|新功能|模型|提示词)")


# ═══════════════════════════════════════════════════════════════════════════════
# Plan item / plan table
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PlanItem:
    id: str
    delay_seconds: float
    action: str


@dataclass
class PlanTable:
    items: list[PlanItem] = field(default_factory=list)
    max_capacity: int = 5
    min_capacity: int = 1

    def to_text(self) -> str:
        if not self.items:
            return "（空）"
        lines = []
        for i, item in enumerate(self.items):
            lines.append(f"{i}. [{item.delay_seconds:.0f}s] {item.action}")
        return "\n".join(lines)

    def pop_executed(self, index: int = 0) -> PlanItem | None:
        """Remove and return the executed item (by index in the current list)."""
        if 0 <= index < len(self.items):
            return self.items.pop(index)
        return None

    def apply_diff(self, text: str) -> None:
        """Apply add/delete/modify diff from the planner LLM.

        Execution order: delete → modify → add  (modify targets indices
        *after* deletions, add appends after modifications).
        """
        data = _extract_json_array(text)
        if not data:
            return

        deletes: list[int] = []
        modifies: list[tuple[int, float, str]] = []
        adds: list[PlanItem] = []

        for entry in data:
            if not isinstance(entry, dict):
                continue
            op = str(entry.get("op", "")).strip()
            try:
                if op == "delete":
                    deletes.append(int(entry["index"]))
                elif op == "modify":
                    idx = int(entry["index"])
                    action = str(entry.get("action", "")).strip()
                    if not action:
                        continue
                    delay = max(0.0, float(entry.get("delay_seconds", 0)))
                    modifies.append((idx, delay, action))
                elif op == "add":
                    action = str(entry.get("action", "")).strip()
                    if action:
                        delay = max(0.0, float(entry.get("delay_seconds", 0)))
                        adds.append(PlanItem(
                            id=str(uuid.uuid4())[:8],
                            delay_seconds=delay,
                            action=action,
                        ))
            except (ValueError, TypeError):
                continue

        # 1. Delete from highest index to lowest
        for idx in sorted(deletes, reverse=True):
            if 0 <= idx < len(self.items):
                self.items.pop(idx)

        # 2. Modify remaining items
        for idx, delay, action in modifies:
            if 0 <= idx < len(self.items):
                self.items[idx].delay_seconds = delay
                self.items[idx].action = action

        # 3. Add new items, re-sort, cap
        if adds:
            self.items.extend(adds)
            self.items.sort(key=lambda x: x.delay_seconds)
            self.items = self.items[:self.max_capacity]


def _extract_json_array(text: str) -> list:
    stripped = text.strip()

    # Strategy 1: markdown code block
    code_match = re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL)
    if code_match:
        candidate = code_match.group(1).strip()
        try:
            value = json.loads(candidate)
            if isinstance(value, list):
                return value
        except json.JSONDecodeError:
            pass

    # Strategy 2: find outermost brackets
    depth = 0
    start = -1
    for i, ch in enumerate(stripped):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = stripped[start:i + 1]
                try:
                    value = json.loads(candidate)
                    if isinstance(value, list):
                        return value
                except json.JSONDecodeError:
                    cleaned = re.sub(r",\s*]", "]", candidate)
                    try:
                        value = json.loads(cleaned)
                        if isinstance(value, list):
                            return value
                    except json.JSONDecodeError:
                        pass

    # Strategy 3: unclosed brackets — auto-close and retry
    if depth > 0 and start >= 0:
        candidate = stripped[start:]
        for _ in range(depth):
            candidate += "]"
        try:
            value = json.loads(candidate)
            if isinstance(value, list):
                return value
        except json.JSONDecodeError:
            # trailing comma before auto-closed ]
            cleaned = re.sub(r",\s*\]", "]", candidate)
            try:
                value = json.loads(cleaned)
                if isinstance(value, list):
                    return value
            except json.JSONDecodeError:
                pass

    logger.debug("_extract_json_array: no valid JSON array found (len=%d)", len(stripped))
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# ImpulsePlanner — planner + executor
# ═══════════════════════════════════════════════════════════════════════════════

class ImpulsePlanner:
    def __init__(
        self,
        *,
        config: dict,
        session,           # ChatSession
        model: str,
        tts_engine,
        portrait_worker,
        machine,           # SystemStateMachine
        agent_config,
        cancel_slot: list,  # shared cancel token list (single element)
        memory_backend,
        chat_stream_fn,
        stt_refine_inline: bool = False,
        bilibili_manager=None,  # BilibiliLiveManager
        live_mode: bool = False,
        subtitle_client=None,   # SubtitleOverlayClient — cleared on TTS_DONE
    ):
        section = config.get("impulse", {})
        if not isinstance(section, dict):
            section = {}
        self.enabled = bool(section.get("enabled", False))
        self.max_plans = max(1, int(section.get("max_plans", 5)))
        self.min_plans = max(0, int(section.get("min_plans", 1)))
        self.planning_model = str(section.get("planning_model", "") or DEFAULT_PLANNING_MODEL)
        self.screen_timeout = max(5, int(section.get("screen_timeout", 45)))
        self.empty_plan_retry_seconds = max(5.0, float(section.get("empty_plan_retry_seconds", 30.0)))
        self.log_plan_table = bool(section.get("log_plan_table", False))
        self.edge_cache_config = edge_cache.config_from_dict(config)

        self.session = session
        self.model = model
        self.tts_engine = tts_engine
        self.portrait_worker = portrait_worker
        self.machine = machine
        self.agent_config = agent_config
        self._cancel_slot = cancel_slot
        self._chat_stream_fn = chat_stream_fn
        self.memory_backend = memory_backend
        self.stt_refine_inline = stt_refine_inline
        self.bilibili_manager = bilibili_manager
        self.live_mode = live_mode
        self.subtitle_client = subtitle_client

        self.plan_table = PlanTable(
            max_capacity=self.max_plans,
            min_capacity=self.min_plans,
        )
        self._executor_thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._consecutive_count = 0
        # Cached danmaku context for the current planning cycle
        self._danmaku_context: str = ""
        self._danmaku_user_list: str = ""

    # ── public API ──────────────────────────────────────────────────────────

    def on_conversation_end(self) -> None:
        """Call after every conversation turn TTS_DONE (user or impulse)."""
        if not self.enabled:
            return
        self.cancel()
        # Don't join if called from within the executor thread (would deadlock)
        current = threading.current_thread()
        if (
            self._executor_thread is not None
            and self._executor_thread.is_alive()
            and self._executor_thread is not current
        ):
            self._executor_thread.join(timeout=2.0)
        self._cancel_event.clear()
        self._executor_thread = threading.Thread(
            target=self._plan_and_execute,
            daemon=True,
        )
        self._executor_thread.start()

    def cancel(self) -> None:
        """Cancel all pending plan execution (sets cancel event)."""
        self._cancel_event.set()

    def reset(self) -> None:
        """Full reset on user interrupt — cancel, clear plan table, reset counter."""
        self._cancel_event.set()
        self._consecutive_count = 0
        with self._lock:
            self.plan_table = PlanTable(
                max_capacity=self.max_plans,
                min_capacity=self.min_plans,
            )

    # ── internal: planning + execution loop ──────────────────────────────────

    def _plan_and_execute(self) -> None:
        try:
            while not self._cancel_event.is_set():
                screen_result = self._capture_screen()
                if self._cancel_event.is_set():
                    return
                edge_page_context = self._read_edge_page_cache()
                if self._cancel_event.is_set():
                    return
                cognition_context = self._read_cognition_context()
                emotion_context = self._read_emotion_context()
                if self._cancel_event.is_set():
                    return
                memories = self._fetch_memories()
                if self._cancel_event.is_set():
                    return

                # Live mode: fetch danmaku context and update cognition
                danmaku_ctx = ""
                user_list_text = ""
                if self.live_mode and self.bilibili_manager is not None:
                    ctx = self.bilibili_manager.get_danmaku_context(max_entries=40)
                    if ctx:
                        danmaku_ctx = ctx
                        # Build user summary list for the planner
                        summaries = self.bilibili_manager.get_user_summaries()
                        if summaries:
                            user_lines = []
                            for user, _text, cnt in summaries:
                                user_lines.append(f"  {user}（{cnt}条）")
                            user_list_text = "\n".join(user_lines)

                # Call planner OUTSIDE the lock (it does HTTP, ~2s)
                diff_text = self._call_planner(
                    screen_result, memories,
                    edge_page_context=edge_page_context,
                    cognition_context=cognition_context,
                    emotion_context=emotion_context,
                    danmaku_context=danmaku_ctx,
                    user_list_text=user_list_text,
                )
                if diff_text:
                    with self._lock:
                        self.plan_table.apply_diff(diff_text)

                if self._cancel_event.is_set():
                    return

                items = list(self.plan_table.items)

                if self.log_plan_table:
                    if items:
                        print(f"\n  [impulse] plan table ({len(items)} items):")
                        for i, it in enumerate(items):
                            print(f"    {i+1}. [{it.delay_seconds:.0f}s] {it.action}")
                    else:
                        print(f"\n  [impulse] plan table: (empty)")

                if not items:
                    if not self._idle_wait(self.empty_plan_retry_seconds):
                        return
                    continue

                item = items[0]

                if item.delay_seconds > 0:
                    if not self._idle_wait(item.delay_seconds):
                        return
                    # Re-check: item might have been modified/reordered by another
                    # planning cycle while we waited.  Grab the current first item.
                    with self._lock:
                        items = list(self.plan_table.items)
                    if not items:
                        continue
                    item = items[0]

                if self._cancel_event.is_set():
                    return

                self._execute_item(item)
                # Remove executed item from plan table
                with self._lock:
                    if self.plan_table.items and self.plan_table.items[0].id == item.id:
                        self.plan_table.pop_executed(0)

        except Exception as exc:
            logger.warning("impulse plan+execute failed: %s", exc)

    def _capture_screen(self) -> str:
        """Read latest screen analysis from cache (zero-cost, no API call)."""
        try:
            cache = screen_interest.get_cache()
            result = cache.content()
            if result:
                return result
            return ""
        except Exception as exc:
            logger.warning("impulse screen cache read failed: %s", exc)
            return ""

    def _read_edge_page_cache(self) -> str:
        """Read the latest Edge page cache for planner context."""
        if not self.edge_cache_config.enabled:
            return ""
        try:
            return edge_cache.format_for_prompt(
                self.edge_cache_config.cache_file,
                max_chars=max(1000, min(self.edge_cache_config.max_chars, 4000)),
            )
        except Exception as exc:
            logger.warning("impulse edge page cache read failed: %s", exc)
            return ""

    def _read_cognition_context(self) -> str:
        """Read the local cognition runtime cache for planner context."""
        try:
            cognition = getattr(self.session, "cognition", None)
            if cognition and hasattr(cognition, "get_context"):
                return cognition.get_context() or ""
        except Exception as exc:
            logger.warning("impulse cognition context read failed: %s", exc)
        return ""

    def _read_emotion_context(self) -> str:
        """Read the current emotion layer for planner context."""
        try:
            emotion = getattr(self.session, "emotion", None)
            if emotion and hasattr(emotion, "get_context"):
                return emotion.get_context() or ""
        except Exception as exc:
            logger.warning("impulse emotion context read failed: %s", exc)
        return ""

    def _fetch_memories(self) -> str:
        """Fetch related memories using context summary + last 4 rounds."""
        query_parts: list[str] = []
        if self.session.summary:
            query_parts.append(self.session.summary)

        # Last 4 rounds = last 8 messages in history
        recent = self.session.history[-8:] if len(self.session.history) >= 8 else list(self.session.history)
        for msg in recent:
            content = str(msg.get("content", ""))
            if content:
                query_parts.append(content[:200])

        query = " ".join(query_parts)[:1000]
        if not query.strip():
            return ""

        try:
            ctx = self.memory_backend.get_context(query, user_id=self.session.character_id)
            return ctx if ctx else ""
        except Exception:
            return ""

    def _call_planner(
        self,
        screen_result: str,
        memories: str,
        edge_page_context: str = "",
        cognition_context: str = "",
        emotion_context: str = "",
        danmaku_context: str = "",
        user_list_text: str = "",
    ) -> str:
        """Call planning LLM and return raw diff text (not a PlanTable).

        The caller applies ``apply_diff()`` to merge results into the existing
        plan table.
        """
        from kokoro import config as cfg
        from kokoro import token_usage

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        summary = self.session.summary or "（无）"

        # Recent 4 rounds of conversation
        recent_history = self.session.history[-8:] if len(self.session.history) >= 8 else list(self.session.history)
        recent_text = _format_history(recent_history, user_name=getattr(self.session, 'user_name', '你')) if recent_history else "（无）"

        screen_text = screen_result or "（无屏幕内容）"
        if edge_page_context:
            screen_text = _prompts.format_prompt(
                "impulse.edge_page_context",
                context=edge_page_context,
            )
            screen_text = f"{screen_result or '（无屏幕内容）'}\n\n{screen_text}"
        memory_text = memories or "（无相关记忆）"
        existing_text = self.plan_table.to_text()
        layer_context_parts: list[str] = []
        if cognition_context:
            layer_context_parts.append(f"【认知缓存】\n{cognition_context}")
        if emotion_context:
            layer_context_parts.append(f"【当前情绪】\n{emotion_context}")
        layer_context = "\n\n".join(layer_context_parts)

        # Build system prompt — add live hint if in live mode
        if self.live_mode and danmaku_context:
            live_hint = _prompts.get("bilibili_live.live_system_hint", "")
            if live_hint:
                system_prompt = _prompts.format_prompt(
                    "impulse.planner_system",
                    name=self.session.character_name,
                    min_plans=str(self.min_plans),
                    max_plans=str(self.max_plans),
                    character_context=f"{self.session.system_prompt[:600]}\n\n{live_hint}",
                )
            else:
                system_prompt = _prompts.format_prompt(
                    "impulse.planner_system",
                    name=self.session.character_name,
                    min_plans=str(self.min_plans),
                    max_plans=str(self.max_plans),
                    character_context=self.session.system_prompt[:800],
                )
        else:
            system_prompt = _prompts.format_prompt(
                "impulse.planner_system",
                name=self.session.character_name,
                min_plans=str(self.min_plans),
                max_plans=str(self.max_plans),
                character_context=self.session.system_prompt[:800],
            )

        # Build user prompt — append danmaku context if available
        user_prompt = _prompts.format_prompt(
            "impulse.planner_user",
            timestamp=now,
            summary=summary,
            recent_history=recent_text,
            memories=memory_text,
            screen_content=screen_text,
            existing_plans=existing_text,
        )
        if layer_context:
            user_prompt += f"\n\n{layer_context}"
        if danmaku_context and user_list_text:
            live_extra = _prompts.format_prompt(
                "bilibili_live.planner_live_context",
                danmaku_text=danmaku_context,
                user_list=user_list_text,
            )
            user_prompt += f"\n\n{live_extra}"

        model = self.planning_model
        openai_compatible = False
        if cfg.is_deepseek_model(model):
            api_key = cfg.deepseek_api_key()
            api_url = cfg.deepseek_url()
            openai_compatible = True
        elif model.lower().startswith("charglm"):
            api_key = cfg.charglm_api_key()
            api_url = self.session.character_config.get("llm_url") or cfg.llm_url()
            openai_compatible = True
        else:
            api_key = ""
            api_url = cfg.llm_url()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            import requests

            headers = {"Content-Type": "application/json"}
            if openai_compatible:
                base_url = api_url.rstrip("/")
                if not re.search(r"/v\d+$", base_url):
                    base_url += "/v1"
                headers["Authorization"] = f"Bearer {api_key}"
                resp = requests.post(
                    f"{base_url}/chat/completions",
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": 0.7,
                        "max_tokens": 1024,
                    },
                    headers=headers,
                    timeout=60,
                )
                if resp.status_code >= 400:
                    if self.log_plan_table:
                        print(f"\n  [impulse] planner HTTP {resp.status_code}: {_preview(resp.text)}")
                    return ""
                data = resp.json()
                if "choices" not in data:
                    if self.log_plan_table:
                        print(f"\n  [impulse] planner response missing choices: {_preview(json.dumps(data, ensure_ascii=False))}")
                    return ""
                usage = data.get("usage", {})
                pt = int(usage.get("prompt_tokens", 0))
                ct = int(usage.get("completion_tokens", 0))
                if pt or ct:
                    token_usage.record(model, "impulse_plan", pt, ct)
                text = data["choices"][0]["message"]["content"]
            else:
                resp = requests.post(
                    f"{api_url}/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": False,
                        "options": {"temperature": 0.7, "num_predict": 1024},
                    },
                    timeout=60,
                )
                if resp.status_code >= 400:
                    if self.log_plan_table:
                        print(f"\n  [impulse] planner HTTP {resp.status_code}: {_preview(resp.text)}")
                    return ""
                data = resp.json()
                pt = int(data.get("prompt_eval_count", 0))
                ct = int(data.get("eval_count", 0))
                if pt or ct:
                    token_usage.record(model, "impulse_plan", pt, ct)
                text = data.get("message", {}).get("content", "")

            logger.debug("planner response: %s", text[:300])
            return text
        except Exception as exc:
            if self.log_plan_table:
                print(f"\n  [impulse] planner call failed: {type(exc).__name__}: {exc}")
            logger.warning("impulse planner LLM call failed: %s", exc)
            return ""

    def _idle_wait(self, seconds: float) -> bool:
        """Wait for `seconds` of idle time. Pauses countdown when system is busy.
        Returns False if cancelled."""
        elapsed = 0.0
        while elapsed < seconds:
            if self._cancel_event.is_set():
                return False
            if self.machine.is_idle or self.machine.state == self.machine.state.__class__.SCREEN_WATCHING:
                time.sleep(0.25)
                elapsed += 0.25
            else:
                time.sleep(0.25)
        return True

    def _execute_item(self, item: PlanItem) -> None:
        """Generate and speak an impulse utterance for a plan item.

        Runs inline in the executor thread. After TTS finishes, on_conversation_end
        is called by the completion path to trigger re-planning.
        """
        import traceback
        from kokoro import state_machine as sm
        from kokoro import token_usage

        print(f"\n  [impulse] _execute_item: action=\"{item.action[:50]}\"")

        # Claim the conversation slot atomically
        if not self.machine.emit(sm.SystemEvent.PROACTIVE_TRIGGERED):
            print(f"  [impulse] PROACTIVE_TRIGGERED REJECTED (state={self.machine.state})")
            return
        print(f"  [impulse] PROACTIVE_TRIGGERED OK")

        self.machine.set_proactive_state(sm.ProactiveState.EXECUTING)
        impulse_cancel = threading.Event()
        self._cancel_slot[0] = impulse_cancel

        try:
            # Build messages: system prompt + history + impulse trigger
            messages = [{"role": "system", "content": self.session.system_prompt}]
            messages.extend(self.session.history)

            # Inject conversation summary
            if self.session.summary:
                messages.append({"role": "system", "content": f"【对话摘要】\n{self.session.summary}"})

            # Inject screen contexts
            if self.session.screen_contexts:
                screen_prefix = _prompts.get("chat_session.screen_context_prefix", "")
                screen_text = screen_prefix
                for i, ctx in enumerate(self.session.screen_contexts, 1):
                    screen_text += f"{i}. {ctx}\n"
                messages.append({"role": "system", "content": screen_text})

            # Live mode: inject danmaku context as extra system context
            if self.live_mode and self.bilibili_manager is not None:
                danmaku_ctx = self.bilibili_manager.get_danmaku_context(max_entries=30)
                if danmaku_ctx:
                    live_hint = _prompts.get("bilibili_live.live_system_hint", "")
                    messages.append({
                        "role": "system",
                        "content": f"{live_hint}\n{danmaku_ctx}",
                    })

            # Impulse trigger message: tells the LLM what to talk about.
            trigger_content = _prompts.format_prompt(
                "impulse.trigger_system",
                action=item.action,
            )
            messages.append({"role": "system", "content": trigger_content})

            # Inject memory context
            memory_ctx = self.memory_backend.get_context(
                item.action, user_id=self.session.character_id,
            )
            if memory_ctx:
                messages.append({"role": "system", "content": memory_ctx})

            # The user message is the trigger instruction (not shown to user)
            user_trigger = _prompts.format_prompt(
                "impulse.trigger_user",
            ) or _prompts.get("impulse.trigger_user_fallback", "请以角色身份直接说出现在要说的话。")
            messages.append({"role": "user", "content": user_trigger})

            print(
                f"\n  [impulse] plan={item.id} "
                f"delay={item.delay_seconds:.0f}s action=\"{item.action[:60]}\""
            )

            try:
                reply, cancelled = self._chat_stream_fn(
                    messages,
                    self.session.character_name,
                    self.model,
                    self.tts_engine,
                    cancel_event=impulse_cancel,
                    character_config=self.session.character_config,
                    agent_config=self.agent_config,
                    usage_callback=token_usage.make_callback(self.model, "impulse"),
                    tool_context=dict(
                        session=self.session,
                        memory_backend=self.memory_backend,
                        character_id=self.session.character_id,
                    ),
                )
            except Exception as exc:
                print(f"\n[impulse error] {type(exc).__name__}: {exc}")
                self.machine.emit_error("impulse_stream")
                self.machine.set_proactive_state(sm.ProactiveState.DISABLED if not self.enabled else sm.ProactiveState.ACCRUING)
                self._cancel_slot[0] = None
                return

            if cancelled:
                self._cancel_slot[0] = None
                self.machine.set_proactive_state(sm.ProactiveState.DISABLED if not self.enabled else sm.ProactiveState.ACCRUING)
                return

            if reply and not _META_TOPIC_RE.search(reply):
                self.session.history.append({"role": "assistant", "content": reply})
                if len(self.session.history) > self.session.max_history * 2:
                    self.session.history[:] = self.session.history[-self.session.max_history * 2:]
                if self.portrait_worker:
                    self.portrait_worker.submit("", reply)

            # LLM done → SPEAKING
            self.machine.emit(sm.SystemEvent.LLM_DONE)

            # Re-plan immediately after LLM output (don't wait for TTS)
            self.on_conversation_end()

            if self.tts_engine:
                self.machine.set_tts_state(sm.TTSState.STREAMING)

            # Wait for TTS to finish
            if self.tts_engine:
                while self.tts_engine.is_playing and not impulse_cancel.is_set():
                    time.sleep(0.1)
                if impulse_cancel.is_set():
                    self._cancel_slot[0] = None
                    self.machine.set_proactive_state(sm.ProactiveState.DISABLED if not self.enabled else sm.ProactiveState.ACCRUING)
                    return
                self.tts_engine.prepare()

            # TTS done → back to IDLE
            self.machine.set_tts_state(sm.TTSState.IDLE)
            self.machine.emit(sm.SystemEvent.TTS_DONE)
            self.machine.reset_error_count()
            if self.subtitle_client:
                self.subtitle_client.clear()
            self.machine.set_proactive_state(sm.ProactiveState.ACCRUING)

        except Exception as exc:
            print(f"\n[impulse error] {type(exc).__name__}: {exc}")
            traceback.print_exc()
            self.machine.emit_error("impulse")
            self.machine.set_proactive_state(sm.ProactiveState.DISABLED if not self.enabled else sm.ProactiveState.ACCRUING)
        finally:
            self._cancel_slot[0] = None


# ═══════════════════════════════════════════════════════════════════════════════
# Utilities
# ═══════════════════════════════════════════════════════════════════════════════

def _format_history(messages: list[dict], user_name: str = "你") -> str:
    lines: list[str] = []
    for msg in messages:
        role = user_name if msg.get("role") == "user" else "角色"
        content = str(msg.get("content", ""))[:300]
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _preview(text: str, limit: int = 240) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
