from __future__ import annotations

import json
import threading
import time
import tempfile
import unittest
import urllib.error
from unittest import mock
from pathlib import Path

from kokoro.action.plan import ActionPlan, execute_action_plan
from kokoro.action.tools.search_web.client import format_search_result
from kokoro.core import input_events
from kokoro.core.chat_session import ChatSession
from kokoro.core.inner_stream import InnerStream
from kokoro.life.context_compactor import _clean_digest
from kokoro.life.local_thinking import LocalThinking
from kokoro.life import InformationPool, LifeRuntime, TimeAwareness
import kokoro.life.runtime as life_runtime_mod
from kokoro.life.stream_patch import InnerStreamPatch, apply_inner_stream_patch


class DummyStream:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.saved = False

    def _save(self) -> None:
        self.saved = True

    def apply_patch(self, raw_patch, *, max_chars=None):
        result = apply_inner_stream_patch(self.text, InnerStreamPatch.from_raw(raw_patch), max_chars=max_chars or 1600)
        if result.applied:
            self.text = result.text
            self._save()
        return {"applied": result.applied, "reason": result.reason, "after": self.text}


class DummySession:
    character_id = "test_role"
    character_name = "Test Role"

    def __init__(self) -> None:
        self.inner_stream = DummyStream("I am waiting, still holding the previous thread.")


class FakeLlm:
    def __init__(self, response: str | list[str]) -> None:
        self.responses = list(response) if isinstance(response, list) else [response]
        self.calls: list[tuple[list[dict], dict]] = []

    def chat(self, messages, options=None):
        self.calls.append((messages, dict(options or {})))
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class DummyMemoryBackend:
    def get_context_multi(self, query, user_ids):
        return ""

    def store(self, *args, **kwargs):
        return None


class DummyMemorySystem:
    def __init__(self) -> None:
        self.events = []
        self.context = ""
        self.maintenance_calls = 0

    def append_event(self, event) -> str:
        self.events.append(event.as_json())
        return event.event_id

    def default_context(self, **kwargs) -> str:
        return self.context

    def maintenance_once(self, *, max_batches: int = 1):
        self.maintenance_calls += 1

        class Decision:
            remember = []
            archive = []
            notes = "no new events"

        return [Decision()]


class LifeRuntimeTests(unittest.TestCase):
    def test_information_pool_batches_since_sequence(self) -> None:
        now = {"value": 10.0}
        pool = InformationPool(max_events=4, clock=lambda: now["value"])
        first = pool.add(input_events.build_text_event("one", source="debug"))
        now["value"] = 17.0
        second = pool.add(input_events.build_text_event("two", source="debug"))
        now["value"] = 22.0

        batch = pool.batch_since(first.sequence)

        self.assertEqual([item.sequence for item in batch], [second.sequence])
        formatted = pool.format_batch(batch)
        self.assertIn("two", formatted)
        self.assertIn('age="5s"', formatted)
        self.assertIn('<input_event seq="2"', formatted)
        self.assertIn("</input_event>", formatted)
        self.assertIn("newest waited 5s", "\n".join(pool.timing_lines(batch)))

    def test_time_awareness_renders_elapsed_time_material(self) -> None:
        now = {"value": 100.0}
        time_awareness = TimeAwareness(clock=lambda: now["value"])
        time_awareness.started_at = 90.0
        time_awareness.mark_event(event_type="text")
        now["value"] = 125.0
        rendered = time_awareness.render(pending_lines=["A pending thread has waited 20s."])

        self.assertIn("Runtime elapsed: 35s", rendered)
        self.assertIn("Since last external input: 25s", rendered)
        self.assertIn("A pending thread has waited 20s.", rendered)

    def test_apply_inner_stream_patch_replaces_and_appends_without_semantic_rules(self) -> None:
        patch = InnerStreamPatch.from_raw(
            {
                "patches": [
                    {
                        "op": "replace",
                        "target": "I am waiting",
                        "text": "I notice time passing while I wait",
                    },
                    {"op": "append", "text": "I should remember the unfinished thread."},
                ]
            }
        )

        result = apply_inner_stream_patch("I am waiting, still here.", patch)

        self.assertTrue(result.applied)
        self.assertIn("I notice time passing", result.text)
        self.assertIn("unfinished thread", result.text)

    def test_apply_inner_stream_patch_accepts_replacement_field(self) -> None:
        patch = InnerStreamPatch.from_raw(
            {
                "patches": [
                    {
                        "op": "replace",
                        "target": "I am waiting",
                        "replacement": "I am already answering",
                    }
                ]
            }
        )

        result = apply_inner_stream_patch("I am waiting, still here.", patch)

        self.assertTrue(result.applied)
        self.assertIn("I am already answering", result.text)

    def test_apply_inner_stream_full_text_strips_code_fence(self) -> None:
        patch = InnerStreamPatch.from_raw({"full_text": "```txt\nI remain continuous.\n```"})

        result = apply_inner_stream_patch("old", patch)

        self.assertTrue(result.applied)
        self.assertEqual(result.text, "I remain continuous.")

    def test_apply_inner_stream_full_text_strips_plaintext_marker(self) -> None:
        patch = InnerStreamPatch.from_raw({"full_text": "```plaintext\nI remain continuous.\n```"})

        result = apply_inner_stream_patch("old", patch)

        self.assertTrue(result.applied)
        self.assertEqual(result.text, "I remain continuous.")

    def test_apply_inner_stream_rejects_meta_placeholder_text(self) -> None:
        patch = InnerStreamPatch.from_raw({"full_text": "思考强度：中等，正在进行日常活动。"})

        result = apply_inner_stream_patch("old stream", patch)

        self.assertFalse(result.applied)
        self.assertEqual(result.text, "old stream")

    def test_apply_inner_stream_rejects_empty_placeholder_text(self) -> None:
        patch = InnerStreamPatch.from_raw({"full_text": "(empty)"})

        result = apply_inner_stream_patch("old stream", patch)

        self.assertFalse(result.applied)
        self.assertEqual(result.text, "old stream")

    def test_apply_inner_stream_skips_highly_similar_append(self) -> None:
        current = "还在琢磨民国背景里异能和枪械的力量差，枪弱的话小人物才有能钻的缝。"
        patch = InnerStreamPatch.from_raw(
            {
                "patches": [
                    {
                        "op": "append",
                        "text": "继续想民国背景中异能和枪械的力量差：如果枪弱，小人物才可能找到缝隙。",
                    }
                ]
            }
        )

        result = apply_inner_stream_patch(current, patch)

        self.assertFalse(result.applied)
        self.assertEqual(result.text, current)

    def test_context_digest_cleaner_removes_markdown_wrapping(self) -> None:
        digest = _clean_digest("**当前时间：**\n```plaintext\nunfinished thread\n```")

        self.assertIn("当前时间", digest)
        self.assertIn("unfinished thread", digest)
        self.assertNotIn("```", digest)
        self.assertNotIn("plaintext", digest)

    def test_context_compactor_writes_explicit_compaction_audit(self) -> None:
        from kokoro.life.context_compactor import ContextCompactor

        with tempfile.TemporaryDirectory() as tmp:
            compactor = ContextCompactor(character_id="test_role", root=Path(tmp), llm_call=None, max_chars=500)
            compactor.append_live("A visible event entered the life context.")
            digest = compactor.compact_once(time_context="Runtime elapsed: 10s", inner_stream="I am thinking.")
            audit_path = Path(tmp) / "characters" / "test_role" / "context" / "compaction_audit.jsonl"
            records = [
                json.loads(line)
                for line in audit_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertIn("A visible event", digest)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["type"], "context_compaction")
        self.assertEqual(records[0]["implementation"], "fallback")
        self.assertGreater(records[0]["input_chars"]["live_events"], 0)
        self.assertGreater(records[0]["output_chars"]["recent_digest"], 0)
        self.assertIn("recent_digest", records[0]["paths"])

    def test_inner_stream_exposes_patch_first_entrypoint(self) -> None:
        stream = object.__new__(InnerStream)
        stream.character_id = "test_role"
        stream.character_data = {}
        stream.text = "I am waiting here."
        stream.saved = False
        stream._save = lambda: setattr(stream, "saved", True)

        debug = stream.apply_patch(
            {
                "patches": [
                    {
                        "op": "replace",
                        "target": "I am waiting",
                        "text": "I feel the time passing",
                    }
                ]
            }
        )

        self.assertTrue(debug["applied"])
        self.assertTrue(stream.saved)
        self.assertIn("time passing", stream.text)

    def test_life_runtime_tick_uses_llm_patch_and_writes_inner_stream(self) -> None:
        response = json.dumps(
            {
                "thinking_intensity": 72,
                "inner_stream_patch": {
                    "base_version": 0,
                    "patches": [
                        {
                            "op": "replace",
                            "target": "I am waiting",
                            "text": "I feel time passing and keep the thread alive",
                        }
                    ],
                    "reason": "time awareness changed the inner stream",
                },
                "action_plan": {"actions": []},
            }
        )
        session = DummySession()
        llm = FakeLlm(response)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(input_events.build_text_event("new information", source="debug"))
            result = runtime.tick_once()

        self.assertEqual(result.processed_events, 1)
        self.assertEqual(result.thinking_intensity, 72)
        self.assertTrue(result.patch_applied)
        self.assertTrue(session.inner_stream.saved)
        self.assertIn("time passing", session.inner_stream.text)
        self.assertEqual(llm.calls[-1][1]["function"], "life_tick")
        prompt_text = llm.calls[-1][0][-1]["content"]
        self.assertIn("inner_stream", prompt_text)
        self.assertIn('<life_context source="inner_stream"', prompt_text)
        self.assertIn('<life_context source="event_batch"', prompt_text)
        self.assertIn('max_chars="4000"', prompt_text)
        self.assertIn("0", prompt_text)

    def test_life_runtime_context_fragments_bound_dynamic_prompt_material(self) -> None:
        response = json.dumps({"thinking_intensity": 20, "notes": "quiet"})
        session = DummySession()
        llm = FakeLlm(response)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={
                    "enabled": True,
                    "local_thinking": {"enabled": True},
                    "context_fragment_max_chars": 200,
                },
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(input_events.build_text_event("old_prefix_" + ("x" * 260) + "_new_tail", source="debug"))
            runtime.tick_once()

        prompt_text = llm.calls[-1][0][-1]["content"]
        self.assertIn('<life_context source="event_batch"', prompt_text)
        self.assertIn('max_chars="200"', prompt_text)
        self.assertIn("_new_tail", prompt_text)
        self.assertNotIn("old_prefix_", prompt_text)

    def test_life_runtime_loads_tool_prompt_specs_from_project_root(self) -> None:
        session = DummySession()
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=FakeLlm("{}"),
                root=Path(tmp),
            )
            runtime._available_actions.add("search_web")

            text = runtime._tool_capabilities_text()

        self.assertIn("Tool prompt catalog", text)
        self.assertIn("search_web", text)
        self.assertIn("prepare LLM", text)
        self.assertIn("after LLM", text)
        self.assertNotIn("你在为 search_web 工具提炼搜索请求", text)
        self.assertNotIn("query 要保留当前注意力里的具体对象", text)

    def test_life_runtime_can_opt_in_to_tool_stage_prompts_for_diagnostics(self) -> None:
        session = DummySession()
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={
                    "enabled": True,
                    "local_thinking": {"enabled": True},
                    "include_tool_stage_prompts_in_life_prompt": True,
                    "tool_stage_prompt_max_chars": 180,
                },
                llm=FakeLlm("{}"),
                root=Path(tmp),
            )
            runtime._available_actions.add("search_web")

            text = runtime._tool_capabilities_text()

        self.assertIn("你在为网页搜索工具提炼 query", text)
        self.assertIn("query 必须保留当前注意力里的具体对象", text)

    def test_life_runtime_records_inner_activity_as_memory_events(self) -> None:
        response = json.dumps(
            {
                "thinking_intensity": 60,
                "inner_stream_patch": {
                    "base_version": 0,
                    "patches": [
                        {
                            "op": "append",
                            "text": "I keep the research thread alive.",
                        }
                    ],
                    "reason": "continued internal activity",
                },
                "pending_threads": ["continue researching Minecraft adventure mod structure"],
            }
        )
        session = DummySession()
        session.memory_system = DummyMemorySystem()
        llm = FakeLlm(response)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(input_events.build_text_event("new research clue", source="debug"))
            runtime.tick_once()

        event_types = [event["type"] for event in session.memory_system.events]
        self.assertIn("runtime_input", event_types)
        self.assertIn("context_digest", event_types)
        self.assertIn("inner_thought", event_types)
        self.assertIn("inner_stream_patch_applied", event_types)
        self.assertIn("pending_threads", event_types)

    def test_life_runtime_memory_is_core_not_action_tool(self) -> None:
        response = json.dumps({"thinking_intensity": 20, "notes": "quiet"})
        session = DummySession()
        memory = DummyMemorySystem()
        memory.context = "A relevant remembered research clue."
        session.memory_system = memory
        llm = FakeLlm(response)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(input_events.build_text_event("new clue", source="debug"))
            runtime.tick_once()

        self.assertNotIn("search_memory", runtime._available_actions)
        self.assertNotIn("save_to_memory", runtime._available_actions)
        self.assertNotIn("write_conversation_memory", runtime._available_actions)
        event_types = [event["type"] for event in memory.events]
        self.assertIn("memory_context_presented", event_types)
        self.assertIn("memory_core_cycle", event_types)
        self.assertGreaterEqual(memory.maintenance_calls, 1)

    def test_life_runtime_prompt_trace_writes_llm_parse_and_tool_plan(self) -> None:
        response = json.dumps(
            {
                "thinking_intensity": 50,
                "action_plan": {"actions": [{"id": "t", "tool": "get_current_time", "args": {}}]},
            }
        )
        session = DummySession()
        llm = FakeLlm(response)
        with tempfile.TemporaryDirectory() as tmp:
            trace_root = Path(tmp) / "prompt_trace"
            runtime = LifeRuntime(
                session=session,
                section={
                    "enabled": True,
                    "local_thinking": {"enabled": True},
                    "prompt_trace_dir": str(trace_root),
                    "tool_followup_rounds": 0,
                },
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(input_events.build_text_event("new information", source="debug"))
            runtime.tick_once()

            trace_dirs = [path for path in trace_root.iterdir() if path.is_dir()]

            self.assertEqual(len(trace_dirs), 1)
            self.assertTrue((trace_dirs[0] / "llm_raw.txt").exists())
            self.assertTrue((trace_dirs[0] / "parsed.json").exists())
            self.assertTrue((trace_dirs[0] / "tool_plan.json").exists())
            self.assertIn("get_current_time", (trace_dirs[0] / "tool_plan.json").read_text(encoding="utf-8"))

    def test_life_runtime_feeds_tool_results_back_in_same_tick(self) -> None:
        initial = json.dumps(
            {
                "thinking_intensity": 50,
                "action_plan": {"actions": [{"id": "t", "tool": "get_current_time", "args": {}}]},
            }
        )
        followup = json.dumps(
            {
                "thinking_intensity": 65,
                "inner_stream_patch": {
                    "patches": [{"op": "append", "text": "I notice the returned time and keep the same thread moving."}],
                    "reason": "the tool result came back inside the same life tick",
                },
            }
        )
        session = DummySession()
        llm = FakeLlm(["compressed context", initial, followup])
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(input_events.build_text_event("check the time and keep thinking", source="debug"))
            result = runtime.tick_once()

            self.assertIn("same_tick_tool_results", runtime.compactor.tool_results_digest())

        functions = [call[1]["function"] for call in llm.calls]
        self.assertEqual(functions, ["life_context_compact", "life_tick", "life_tick"])
        self.assertTrue(result.patch_applied)
        self.assertEqual(result.thinking_intensity, 65)
        self.assertIn("returned time", session.inner_stream.text)

    def test_life_runtime_time_context_includes_event_batch_age(self) -> None:
        response = json.dumps({"thinking_intensity": 40})
        session = DummySession()
        llm = FakeLlm(["compressed context", response])
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )
            runtime.pool.clock = lambda: 100.0
            runtime.submit(input_events.build_text_event("an event that waited", source="debug"))
            runtime.pool.clock = lambda: 145.0
            runtime.tick_once()

        rendered_messages = "\n".join(str(message.get("content", "")) for message in llm.calls[1][0])
        self.assertIn("Current event batch: 1 item(s), oldest waited 45s", rendered_messages)

    def test_life_runtime_defers_context_compaction_for_foreground_chat(self) -> None:
        response = json.dumps({"thinking_intensity": 40, "notes": "answer the live message first"})
        session = DummySession()
        llm = FakeLlm(response)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(
                input_events.build_chat_environment_event(
                    "private chat message",
                    source="qq",
                    metadata={"message_type": "private", "conversation_id": "private:test"},
                    priority="high",
                )
            )
            runtime.tick_once()

        functions = [call[1]["function"] for call in llm.calls]
        self.assertEqual(functions, ["life_tick"])

    def test_life_runtime_uses_smaller_foreground_tick_budget(self) -> None:
        response = json.dumps({"thinking_intensity": 40, "notes": "short live response path"})
        session = DummySession()
        llm = FakeLlm(response)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={
                    "enabled": True,
                    "local_thinking": {"enabled": True},
                    "tick_max_tokens": 900,
                    "foreground_tick_max_tokens": 333,
                    "context_fragment_max_chars": 5000,
                    "foreground_context_fragment_max_chars": 777,
                },
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(
                input_events.build_chat_environment_event(
                    "private chat message",
                    source="qq",
                    metadata={"message_type": "private", "conversation_id": "private:test"},
                    priority="high",
                )
            )
            runtime.tick_once()

        options = llm.calls[-1][1]
        prompt_text = "\n".join(str(message.get("content", "")) for message in llm.calls[-1][0])
        self.assertEqual(options["max_tokens"], 333)
        self.assertIn('max_chars="777"', prompt_text)

    def test_life_runtime_uses_smaller_idle_tick_budget(self) -> None:
        response = json.dumps({"thinking_intensity": 30, "notes": "light idle thought"})
        session = DummySession()
        llm = FakeLlm(response)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={
                    "enabled": True,
                    "local_thinking": {"enabled": True},
                    "tick_max_tokens": 900,
                    "idle_tick_max_tokens": 222,
                    "context_fragment_max_chars": 5000,
                    "idle_context_fragment_max_chars": 666,
                },
                llm=llm,
                root=Path(tmp),
            )
            runtime.tick_once(force=True)

        options = llm.calls[-1][1]
        prompt_text = "\n".join(str(message.get("content", "")) for message in llm.calls[-1][0])
        self.assertEqual(options["max_tokens"], 222)
        self.assertIn('max_chars="666"', prompt_text)

    def test_life_runtime_defers_memory_core_when_input_arrives_during_idle_tick(self) -> None:
        response = json.dumps({"thinking_intensity": 40, "notes": "idle thought"})
        session = DummySession()
        memory = DummyMemorySystem()
        session.memory_system = memory
        llm = FakeLlm(response)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={
                    "enabled": True,
                    "local_thinking": {"enabled": True},
                    "memory_core_interval_seconds": 0,
                },
                llm=llm,
                root=Path(tmp),
            )
            original_think = runtime._think

            def think_and_receive_input(**kwargs):
                runtime.submit(input_events.build_text_event("arrived while thinking", source="debug", priority="high"))
                return original_think(**kwargs)

            runtime._think = think_and_receive_input
            runtime.tick_once(force=True)

        self.assertEqual(memory.maintenance_calls, 0)

    def test_life_runtime_loop_prioritizes_pending_input_after_wait_race(self) -> None:
        session = DummySession()
        llm = FakeLlm("{}")
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )

            class RaceWake:
                def wait(self, timeout=None):
                    return False

                def clear(self):
                    runtime.submit(input_events.build_text_event("arrived during wake clear", source="debug", priority="high"))

                def set(self):
                    pass

            forces = []
            runtime._wake = RaceWake()

            def fake_tick_once(*, force=False):
                forces.append(force)
                runtime._stop.set()

            runtime.tick_once = fake_tick_once
            runtime._run()

        self.assertEqual(forces, [False])

    def test_life_runtime_repairs_invalid_json_tick_output(self) -> None:
        bad = '{"thinking_intensity": 61, "inner_stream_patch": {"patches": [{"op": "append", "text": "I keep the repaired thread alive."}]'
        repaired = json.dumps(
            {
                "thinking_intensity": 61,
                "inner_stream_patch": {
                    "patches": [{"op": "append", "text": "I keep the repaired thread alive."}]
                },
            }
        )
        session = DummySession()
        llm = FakeLlm(["compressed context", bad, repaired])
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(input_events.build_text_event("new information", source="debug"))
            result = runtime.tick_once()

        self.assertTrue(result.patch_applied)
        self.assertEqual(result.thinking_intensity, 61)
        self.assertIn("repaired thread", session.inner_stream.text)
        self.assertEqual(llm.calls[-1][1]["function"], "life_tick_json_repair")

    def test_life_runtime_records_llm_pending_threads(self) -> None:
        response = json.dumps(
            {
                "thinking_intensity": 60,
                "inner_stream_patch": {"patches": [{"op": "append", "text": "I keep one loose thread in view."}]},
                "pending_threads": "The tool result has not come back yet; keep it in view.",
            }
        )
        session = DummySession()
        llm = FakeLlm(response)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(input_events.build_text_event("new information", source="debug"))
            runtime.tick_once()

            self.assertIn("not come back yet", runtime.compactor.pending_threads())

    def test_life_runtime_ignores_empty_pending_threads_marker(self) -> None:
        response = json.dumps(
            {
                "thinking_intensity": 60,
                "inner_stream_patch": {"patches": [{"op": "append", "text": "I keep moving."}]},
                "pending_threads": "none",
            }
        )
        session = DummySession()
        llm = FakeLlm(response)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(input_events.build_text_event("new information", source="debug"))
            runtime.tick_once()

            self.assertEqual(runtime.compactor.pending_threads().strip(), "")

    def test_life_runtime_action_result_enters_tool_results_digest(self) -> None:
        session = DummySession()
        llm = FakeLlm("{}")
        event = input_events.build_action_result_event(
            "tool finished with useful context",
            source="debug_tool",
            metadata={"action": "debug_tool"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(event)

            self.assertIn("tool finished", runtime.compactor.tool_results_digest())

    def test_life_runtime_preserves_zero_result_merge_window(self) -> None:
        session = DummySession()
        llm = FakeLlm("{}")
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={
                    "enabled": True,
                    "result_merge_window_seconds": 0.0,
                    "local_thinking": {"enabled": True},
                },
                llm=llm,
                root=Path(tmp),
            )

        self.assertEqual(runtime.action_runtime.merge_window_seconds, 0.0)

    def test_life_runtime_does_not_expose_unavailable_output_tools(self) -> None:
        session = DummySession()
        llm = FakeLlm("{}")
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )

        text = runtime._tool_capabilities_text()

        self.assertIn("get_current_time", text)
        self.assertNotIn("say_precomputed", text)
        self.assertNotIn("send_qq_message", text)
        self.assertNotIn("search_web", text)
        self.assertNotIn("claude_code_exec", text)

    def test_life_runtime_clips_tool_schema_descriptions(self) -> None:
        session = DummySession()
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={
                    "enabled": True,
                    "local_thinking": {"enabled": True},
                    "tool_schema_description_max_chars": 32,
                },
                llm=FakeLlm("{}"),
                root=Path(tmp),
            )
            runtime._available_actions.add("get_current_time")

            text = runtime._tool_capabilities_text()

        schema_lines = [line for line in text.splitlines() if line.startswith("- get_current_time:")]
        self.assertTrue(schema_lines)
        self.assertLessEqual(len(schema_lines[0]), 80)

    def test_life_runtime_rejects_unavailable_or_incomplete_action_plan(self) -> None:
        session = DummySession()
        llm = FakeLlm("{}")
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )

        unavailable = ActionPlan.from_dict({"actions": [{"id": "s", "tool": "say_precomputed", "args": {}}]})
        memory_tool = ActionPlan.from_dict({"actions": [{"id": "w", "tool": "write_conversation_memory", "args": {}}]})
        memory_misuse = ActionPlan.from_dict(
            {
                "actions": [
                    {
                        "id": "m",
                        "tool": "write_conversation_memory",
                        "args": {"reply": "I am saving my inner stream here."},
                    }
                ]
            }
        )

        self.assertIn("not available", runtime._validate_action_plan(unavailable))
        self.assertIn("not available", runtime._validate_action_plan(memory_tool))
        self.assertIn("not available", runtime._validate_action_plan(memory_misuse))

    def test_life_runtime_ignores_empty_action_plan(self) -> None:
        response = json.dumps(
            {
                "thinking_intensity": 50,
                "inner_stream_patch": {"patches": [{"op": "append", "text": "I keep the thread in view."}]},
                "action_plan": {"reason": "nothing to do", "actions": []},
            }
        )
        session = DummySession()
        llm = FakeLlm(response)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(input_events.build_text_event("new information", source="debug"))
            result = runtime.tick_once()

        self.assertIsNone(result.action_plan)

    def test_life_runtime_reports_rejected_action_plan_status(self) -> None:
        response = json.dumps(
            {
                "thinking_intensity": 50,
                "action_plan": {"actions": [{"id": "w", "tool": "write_conversation_memory", "args": {}}]},
            }
        )
        session = DummySession()
        llm = FakeLlm(response)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(input_events.build_text_event("new information", source="debug"))
            result = runtime.tick_once()

        self.assertEqual(result.action_plan_status, "rejected")
        self.assertIn("not available", result.action_plan_error)

    def test_local_thinking_openai_style_uses_chat_completions(self) -> None:
        thinker = LocalThinking(
            {
                "enabled": True,
                "model": "local-model",
                "base_url": "http://127.0.0.1:14515/v1",
                "api_style": "openai",
            }
        )
        calls = []

        def fake_post(url, payload, *, timeout):
            calls.append((url, payload, timeout))
            return {"choices": [{"message": {"content": "ok"}}]}

        thinker._post_json = fake_post

        result = thinker.chat([{"role": "user", "content": "hi"}], {"function": "test"})

        self.assertEqual(result, "ok")
        self.assertEqual(calls[0][0], "http://127.0.0.1:14515/v1/chat/completions")
        self.assertEqual(calls[0][1]["model"], "local-model")

    def test_local_thinking_auto_falls_back_to_openai_on_ollama_404(self) -> None:
        thinker = LocalThinking(
            {
                "enabled": True,
                "model": "local-model",
                "base_url": "http://127.0.0.1:14515",
                "api_style": "auto",
            }
        )
        calls = []

        def fake_post(url, payload, *, timeout):
            calls.append(url)
            if url.endswith("/api/chat"):
                raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None, fp=None)
            return {"choices": [{"message": {"content": "fallback ok"}}]}

        thinker._post_json = fake_post

        result = thinker.chat([{"role": "user", "content": "hi"}], {"function": "test"})

        self.assertEqual(result, "fallback ok")
        self.assertEqual(calls, ["http://127.0.0.1:14515/api/chat", "http://127.0.0.1:14515/v1/chat/completions"])

    def test_local_thinking_routes_foreground_tool_select_to_primary_model(self) -> None:
        thinker = LocalThinking(
            {
                "enabled": True,
                "primary_model": "deepseek-v4-flash",
                "auxiliary_model": "qwen2.5:7b",
            }
        )

        self.assertEqual(thinker._model_for_function("life_tick"), "deepseek-v4-flash")
        self.assertEqual(thinker._model_for_function("life_tool_select"), "deepseek-v4-flash")
        self.assertEqual(thinker._model_for_function("life_context_compact"), "qwen2.5:7b")
        self.assertEqual(thinker._model_for_function("memory_experience_workspace"), "qwen2.5:7b")

    def test_local_thinking_priority_queue_runs_life_tick_before_memory(self) -> None:
        thinker = LocalThinking(
            {
                "enabled": True,
                "model": "local-model",
                "base_url": "http://127.0.0.1:14515",
                "api_style": "openai",
            }
        )
        executed = []
        original_ensure_worker = thinker._ensure_worker
        thinker._ensure_worker = lambda: None

        def fake_chat_now(messages, options):
            function = options["function"]
            executed.append(function)
            return f"done:{function}"

        thinker._chat_now = fake_chat_now
        results = {}
        low = threading.Thread(
            target=lambda: results.update(memory=thinker.chat([], {"function": "memory_lifecycle"})),
            daemon=True,
        )
        high = threading.Thread(
            target=lambda: results.update(life=thinker.chat([], {"function": "life_tick"})),
            daemon=True,
        )
        low.start()
        high.start()
        deadline = time.monotonic() + 1.0
        while thinker._queue.qsize() < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        thinker._ensure_worker = original_ensure_worker
        thinker._ensure_worker()
        low.join(timeout=1.0)
        high.join(timeout=1.0)

        self.assertEqual(executed[:2], ["life_tick", "memory_lifecycle"])
        self.assertEqual(results["life"], "done:life_tick")
        self.assertEqual(results["memory"], "done:memory_lifecycle")

    def test_local_thinking_coalesces_stale_memory_calls(self) -> None:
        thinker = LocalThinking(
            {
                "enabled": True,
                "model": "local-model",
                "base_url": "http://127.0.0.1:14515",
                "api_style": "openai",
            }
        )
        executed = []
        original_ensure_worker = thinker._ensure_worker
        thinker._ensure_worker = lambda: None

        def fake_chat_now(messages, options):
            function = options["function"]
            marker = messages[0]["content"]
            executed.append((function, marker))
            return f"done:{marker}"

        thinker._chat_now = fake_chat_now
        results = {}
        first = threading.Thread(
            target=lambda: results.update(first=thinker.chat([{"role": "user", "content": "old"}], {"function": "memory_experience_workspace"})),
            daemon=True,
        )
        second = threading.Thread(
            target=lambda: results.update(second=thinker.chat([{"role": "user", "content": "new"}], {"function": "memory_experience_workspace"})),
            daemon=True,
        )
        first.start()
        deadline = time.monotonic() + 1.0
        while thinker._queue.qsize() < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        second.start()
        deadline = time.monotonic() + 1.0
        while thinker._queue.qsize() < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        first.join(timeout=1.0)
        thinker._ensure_worker = original_ensure_worker
        thinker._ensure_worker()
        second.join(timeout=1.0)

        self.assertEqual(results["first"], "")
        self.assertEqual(results["second"], "done:new")
        self.assertEqual(executed, [("memory_experience_workspace", "new")])

    def test_life_debug_script_uses_memory_candidate_inputs_not_forced_memory_tools(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "scripts" / "run_life_runtime_debug.py").read_text(encoding="utf-8")

        self.assertIn("--memory-event", script)
        self.assertIn("life_runtime_debug_memory_candidate", script)
        self.assertNotIn("--force-memory-tool", script)
        self.assertNotIn("life_debug.force_memory_tool", script)

    def test_life_debug_analyzer_reports_continuity_evidence(self) -> None:
        from scripts.analyze_life_runtime_debug import analyze_run

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "run_summary.json").write_text(
                json.dumps(
                    {
                        "character": "lerwa",
                        "scripted": False,
                        "tool_results_digest": "[same_tick_tool_results]\n- ok",
                        "inner_stream": "I keep thinking.",
                        "context_digest": "recent context",
                        "pending_threads": "unfinished thread",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            records = [
                {"event": "life_debug.start", "monotonic": 10.0},
                {"event": "life.runtime.prompt_rendered", "monotonic": 10.1},
                {"event": "life.local_thinking.start", "function": "life_tick", "monotonic": 10.2},
                {
                    "event": "life.runtime.tick_done",
                    "monotonic": 20.0,
                    "processed_events": 1,
                    "patch_applied": True,
                    "has_action_plan": True,
                    "action_plan_status": "executed",
                },
                {"event": "life.runtime.tool_results.same_tick_context", "monotonic": 20.1},
                {"event": "life.context.tool_result_append", "monotonic": 20.2},
                {"event": "life_debug.done", "monotonic": 70.0},
            ]
            (run_dir / "lifecycle_trace.jsonl").write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )
            audit_dir = run_dir / "characters" / "lerwa" / "context"
            audit_dir.mkdir(parents=True)
            (audit_dir / "compaction_audit.jsonl").write_text(
                json.dumps({"type": "context_compaction"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            audit = analyze_run(run_dir)

        self.assertEqual(audit["tick_count"], 1)
        self.assertEqual(audit["action_plan"]["executed"], 1)
        self.assertTrue(audit["same_tick_tool_feedback"]["present"])
        self.assertEqual(audit["context_continuity"]["compaction_audit_records"], 1)
        self.assertEqual(audit["context_continuity"]["life_tick_llm_calls"], 1)

    def test_web_search_result_format_is_neutral_not_first_person(self) -> None:
        text = format_search_result(
            "Minecraft模组开发任务结构设计",
            {
                "results": [
                    {
                        "title": "Example",
                        "url": "https://example.test",
                        "snippet": "A result snippet.",
                    }
                ]
            },
        )

        self.assertIn("[web_search_result]", text)
        self.assertIn("query: Minecraft模组开发任务结构设计", text)
        self.assertIn("candidate_count:", text)
        self.assertIn("candidates:", text)
        self.assertIn("title: Example", text)
        self.assertNotIn("我刚刚搜索了", text)
        self.assertNotIn("搜索结果：", text)

    def test_web_search_result_format_keeps_prompt_material_compact(self) -> None:
        text = format_search_result(
            "Minecraft adventure mod",
            {
                "results": [
                    {
                        "title": "A very long result title " + ("x" * 200),
                        "url": "https://example.test/" + ("path/" * 80),
                        "snippet": "snippet " + ("y" * 1000),
                    }
                ]
            },
            max_chars=420,
        )

        self.assertLessEqual(len(text), 420)
        self.assertIn("title:", text)
        self.assertIn("url:", text)
        self.assertIn("note:", text)
        self.assertNotIn("query_match_hint", text)

    def test_life_runtime_falls_back_to_full_rewrite_when_patch_misses(self) -> None:
        bad_patch = json.dumps(
            {
                "thinking_intensity": 64,
                "inner_stream_patch": {
                    "patches": [
                        {
                            "op": "replace",
                            "target": "missing target",
                            "text": "new text",
                        }
                    ]
                },
            }
        )
        session = DummySession()
        llm = FakeLlm(["compressed context", bad_patch, "I recover the whole inner stream with time awareness."])
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LifeRuntime(
                session=session,
                section={"enabled": True, "local_thinking": {"enabled": True}},
                llm=llm,
                root=Path(tmp),
            )
            runtime.submit(input_events.build_text_event("new information", source="debug"))
            result = runtime.tick_once()

        self.assertTrue(result.patch_applied)
        self.assertTrue(session.inner_stream.saved)
        self.assertIn("recover the whole inner stream", session.inner_stream.text)
        self.assertEqual(llm.calls[-1][1]["function"], "life_inner_stream_patch_fallback")

    def test_action_plan_preserves_llm_dependencies_without_policy_rules(self) -> None:
        plan = ActionPlan.from_dict(
            {
                "plan_id": "plan_test",
                "actions": [
                    {"id": "read_memory", "tool": "memory.read", "parallel": True},
                    {"id": "read_time", "tool": "time.now", "parallel": True},
                    {"id": "say", "tool": "say", "after": ["read_memory", "read_time"]},
                ],
            }
        )

        levels = [[node.id for node in level] for level in plan.execution_levels()]

        self.assertEqual(levels, [["read_memory", "read_time"], ["say"]])

    def test_execute_action_plan_uses_existing_action_runtime_boundary(self) -> None:
        class Runtime:
            def __init__(self) -> None:
                self.calls = []

            def execute_action_for_result(self, batch, action):
                self.calls.append((batch.cycle_id, action.action, action.action_id))
                return f"done:{action.action}"

        runtime = Runtime()
        plan = ActionPlan.from_dict(
            {
                "plan_id": "plan_test",
                "actions": [
                    {"id": "a", "tool": "first"},
                    {"id": "b", "tool": "second", "after": ["a"]},
                ],
            }
        )

        results = execute_action_plan(plan, runtime)

        self.assertEqual(results, {"a": "done:first", "b": "done:second"})
        self.assertEqual([call[1] for call in runtime.calls], ["first", "second"])

    def test_action_plan_parallel_level_executes_concurrently(self) -> None:
        class Runtime:
            def execute_action_for_result(self, batch, action):
                time.sleep(0.05)
                return f"done:{action.action}"

        plan = ActionPlan.from_dict(
            {
                "actions": [
                    {"id": "a", "tool": "first", "parallel": True},
                    {"id": "b", "tool": "second", "parallel": True},
                ],
            }
        )

        started = time.perf_counter()
        results = execute_action_plan(plan, Runtime())
        elapsed = time.perf_counter() - started

        self.assertEqual(results, {"a": "done:first", "b": "done:second"})
        self.assertLess(elapsed, 0.09)

    def test_chat_session_life_runtime_primary_skips_old_autonomous_loop(self) -> None:
        class FakeLifeRuntime:
            def __init__(self, *, session, section):
                self.session = session
                self.section = section
                self.submitted = []
                self.started = False

            def submit(self, event):
                self.submitted.append(event)

            def start(self):
                self.started = True

        with (
            mock.patch("kokoro.core.config.life_runtime_config", return_value={"enabled": True, "primary": True}),
            mock.patch("kokoro.life.LifeRuntime", FakeLifeRuntime),
        ):
            session = ChatSession(
                character_id="life_test",
                character_data={"name": "Life Test"},
                memory_backend=DummyMemoryBackend(),
                memory_system=object(),
                inner_stream=DummyStream("I am here."),
                cognition=object(),
                emotion=object(),
            )

        self.assertIsInstance(session.life_runtime, FakeLifeRuntime)
        self.assertTrue(session.life_runtime.started)
        self.assertIsNone(session.inner_stream_loop)
        self.assertIsNone(session.autonomous_step)

    def test_chat_session_primary_life_runtime_keeps_tool_runtime_without_old_loop(self) -> None:
        with (
            mock.patch(
                "kokoro.core.config.life_runtime_config",
                return_value={"enabled": True, "primary": True, "local_thinking": {"enabled": False}},
            ),
            mock.patch("kokoro.life.runtime.LifeRuntime.start", lambda self: None),
        ):
            session = ChatSession(
                character_id="life_test",
                character_data={"name": "Life Test"},
                memory_backend=DummyMemoryBackend(),
                memory_system=object(),
                inner_stream=DummyStream("I am here."),
                cognition=object(),
                emotion=object(),
            )

        self.assertIsNotNone(session.life_runtime)
        self.assertIsNotNone(session.life_runtime.action_runtime)
        self.assertIn("get_current_time", session.life_runtime._available_actions)
        self.assertIsNone(session.inner_stream_loop)
        self.assertIsNone(session.autonomous_step)

    def test_chat_session_injected_life_runtime_receives_event_bus_inputs(self) -> None:
        class FakeLifeRuntime:
            section = {"primary": True}

            def __init__(self) -> None:
                self.submitted = []
                self.started = False

            def submit(self, event):
                self.submitted.append(event)

            def start(self):
                self.started = True

        runtime = FakeLifeRuntime()
        with mock.patch("kokoro.core.config.life_runtime_config", return_value={"enabled": False, "primary": False}):
            session = ChatSession(
                character_id="life_test",
                character_data={"name": "Life Test"},
                memory_backend=DummyMemoryBackend(),
                memory_system=object(),
                inner_stream=DummyStream("I am here."),
                cognition=object(),
                emotion=object(),
                life_runtime=runtime,
            )

        event = session.record_input_event("direct debug input", source="debug")

        self.assertTrue(runtime.started)
        self.assertIsNone(session.inner_stream_loop)
        self.assertIsNone(session.autonomous_step)
        self.assertEqual(runtime.submitted[-1], event)

    def test_expression_intent_does_not_overwrite_explicit_tool_message(self) -> None:
        plan = {
            "actions": [
                {
                    "tool": "send_qq_message",
                    "args": {"message": "那——是遇到什么好事了？还是看到什么好玩的？"},
                }
            ]
        }

        result = life_runtime_mod._attach_expression_intent(
            plan,
            "我想回他一句：那——是遇到什么好事了？还是看到什么好玩的？",
        )

        args = result["actions"][0]["args"]
        self.assertEqual(args["message"], "那——是遇到什么好事了？还是看到什么好玩的？")
        self.assertEqual(args["_intent"], "我想回他一句：那——是遇到什么好事了？还是看到什么好玩的？")

    def test_life_runtime_stop_does_not_shutdown_action_runtime_while_thread_is_alive(self) -> None:
        class AliveThread:
            def __init__(self) -> None:
                self.joined = False

            def is_alive(self) -> bool:
                return True

            def join(self, timeout=None) -> None:
                self.joined = True

        class Runtime:
            def __init__(self) -> None:
                self.flushed = False
                self.shutdown_called = False

            def flush_pending(self) -> None:
                self.flushed = True

            def shutdown(self, **kwargs) -> None:
                self.shutdown_called = True

        runtime = object.__new__(LifeRuntime)
        runtime._stop = threading.Event()
        runtime._wake = threading.Event()
        runtime._thread = AliveThread()
        runtime.action_runtime = Runtime()
        runtime.session = DummySession()

        runtime.stop(wait=True, timeout=0.01)

        self.assertTrue(runtime.action_runtime.flushed)
        self.assertFalse(runtime.action_runtime.shutdown_called)


if __name__ == "__main__":
    unittest.main()
