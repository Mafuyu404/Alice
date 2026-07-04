from __future__ import annotations

import json
import time
import tempfile
import unittest
import urllib.error
from unittest import mock
from pathlib import Path

from kokoro.action.plan import ActionPlan, execute_action_plan
from kokoro.core import input_events
from kokoro.core.chat_session import ChatSession
from kokoro.core.inner_stream import InnerStream
from kokoro.life.context_compactor import _clean_digest
from kokoro.life.local_thinking import LocalThinking
from kokoro.life import InformationPool, LifeRuntime, TimeAwareness
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


class LifeRuntimeTests(unittest.TestCase):
    def test_information_pool_batches_since_sequence(self) -> None:
        pool = InformationPool(max_events=4, clock=lambda: 10.0)
        first = pool.add(input_events.build_text_event("one", source="debug"))
        second = pool.add(input_events.build_text_event("two", source="debug"))

        batch = pool.batch_since(first.sequence)

        self.assertEqual([item.sequence for item in batch], [second.sequence])
        self.assertIn("two", pool.format_batch(batch))

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

    def test_context_digest_cleaner_removes_markdown_wrapping(self) -> None:
        digest = _clean_digest("**当前时间：**\n```plaintext\nunfinished thread\n```")

        self.assertIn("当前时间", digest)
        self.assertIn("unfinished thread", digest)
        self.assertNotIn("```", digest)
        self.assertNotIn("plaintext", digest)

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
        self.assertIn("0", prompt_text)

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
        incomplete = ActionPlan.from_dict({"actions": [{"id": "w", "tool": "write_conversation_memory", "args": {}}]})
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
        self.assertIn("missing required args", runtime._validate_action_plan(incomplete))
        self.assertIn("trigger_text", runtime._validate_action_plan(memory_misuse))

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
        self.assertIn("missing required args", result.action_plan_error)

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
                inner_stream=DummyStream("I am here."),
                cognition=object(),
                emotion=object(),
            )

        self.assertIsInstance(session.life_runtime, FakeLifeRuntime)
        self.assertTrue(session.life_runtime.started)
        self.assertIsNone(session.inner_stream_loop)
        self.assertIsNone(session.autonomous_step)


if __name__ == "__main__":
    unittest.main()
