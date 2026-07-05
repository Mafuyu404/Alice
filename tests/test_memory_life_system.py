from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from kokoro.action.model import Action
from kokoro.action.tool_spec import PreparedAction, ToolContext
from kokoro.action.tools.memory.execute import execute_save_to_memory, execute_search_memory
from kokoro.action.tools.memory.prepare import prepare_save_to_memory
from kokoro.core.memory_events import MemoryEventStore, StoredEvent
from kokoro.life import LifeRuntime
from kokoro.memory import create_memory_system
from kokoro.memory.models import MemoryEventDraft, MemoryRecordDraft


class MemoryLifeSystemTests(unittest.TestCase):
    def test_event_log_and_recall_use_character_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            system = create_memory_system(character_id="xuezhi", root=tmp)
            event_id = system.append_event(
                MemoryEventDraft(
                    character_id="xuezhi",
                    content="雪吱继续研究 Minecraft 红石农场。",
                    source="debug_text",
                    event_type="text",
                )
            )
            self.assertTrue(event_id)

            record = system.remember("雪吱在研究 Minecraft 红石农场时，决定之后继续比较漏斗和水流的收集方式。")
            self.assertIsNotNone(record)

            recalled = system.deep_recall("Minecraft 红石农场")
            self.assertIn("Minecraft", recalled)
            self.assertNotIn("突然想起", recalled)

            events = system.event_log.recent_events(limit=5)
            self.assertEqual(events[-1]["event_id"], event_id)

    def test_recall_diffuses_to_nearby_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            system = create_memory_system(character_id="xuezhi", root=tmp)
            first = system.remember("雪吱研究 Minecraft 村民交易大厅。")
            second = system.remember("同一段时间里，她还记录了铁傀儡农场的刷新条件。")
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)

            text = system.deep_recall("村民交易大厅")
            self.assertIn("村民交易大厅", text)
            self.assertIn("铁傀儡", text)

    def test_recall_formats_memory_as_prompt_safe_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            system = create_memory_system(character_id="xuezhi", root=tmp)
            system.write_draft(
                MemoryRecordDraft(
                    character_id="xuezhi",
                    content="""
                    {
                      "thinking_intensity": 50,
                      "inner_stream_patch": {
                        "patches": [
                          {"op": "append", "text": "公开网页没给出可拆的冒险模组案例，我想换到 Modrinth 页面看结构探索。"}
                        ],
                        "reason": "搜索材料改变了研究角度"
                      }
                    }
                    """,
                    keywords=["Minecraft", "冒险模组", "Modrinth"],
                )
            )
            system.write_draft(
                MemoryRecordDraft(
                    character_id="xuezhi",
                    content="我刚刚搜索了：Minecraft 冒险模组设计案例\n搜索结果：一些网页条目",
                    keywords=["Minecraft", "冒险模组"],
                )
            )
            system.write_draft(
                MemoryRecordDraft(
                    character_id="xuezhi",
                    content="刚被带出的记忆材料：\n- 雪吱想比较地牢推进和战利品节奏。\n这些只是被呈现出来的材料，不代表它们一定重要。",
                    keywords=["地牢", "战利品"],
                )
            )

            text = system.deep_recall("Minecraft 冒险模组 Modrinth 地牢")

        self.assertIn("Modrinth", text)
        self.assertIn("地牢推进", text)
        self.assertNotIn("thinking_intensity", text)
        self.assertNotIn("inner_stream_patch", text)
        self.assertNotIn("我刚刚搜索了", text)
        self.assertNotIn("搜索结果：", text)
        self.assertNotIn("刚被带出的记忆材料：\n- 刚被带出的记忆材料", text)

    def test_memory_tool_prefers_life_memory_system(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            system = create_memory_system(character_id="xuezhi", root=tmp)
            session = SimpleNamespace(memory_system=system, summary="", inner_stream=None)
            ctx = ToolContext(session=session, data={"memory_system": system, "character_id": "xuezhi"})

            action = Action(action="save_to_memory", args={"content": "雪吱想继续研究 Minecraft 自动甘蔗机。"})
            result = execute_save_to_memory(
                ctx,
                PreparedAction(action=action, args=dict(action.args), reason="test"),
            )
            self.assertEqual(result.status, "success")
            self.assertTrue(result.metadata["memory_written"])
            self.assertEqual(result.metadata["memory_system"], "life")

            search = Action(action="search_memory", args={"query": "自动甘蔗机"})
            found = execute_search_memory(
                ctx,
                PreparedAction(action=search, args=dict(search.args), reason="test"),
            )
            self.assertEqual(found.status, "success")
            self.assertIn("自动甘蔗机", found.content)

    def test_save_to_memory_prepare_builds_draft_for_execute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            system = create_memory_system(character_id="xuezhi", root=tmp)
            session = SimpleNamespace(memory_system=system, summary="最近在研究 Minecraft。", inner_stream=None)
            ctx = ToolContext(session=session, data={"memory_system": system, "character_id": "xuezhi"})
            action = Action(action="save_to_memory", args={"content": "雪吱想比较漏斗矿车和水流收集。"})

            prepared = prepare_save_to_memory(ctx, action)
            self.assertEqual(prepared.metadata["prepared_by"], "memory_consolidator")
            self.assertIn("memory_draft", prepared.metadata)

            result = execute_save_to_memory(ctx, prepared)
            self.assertEqual(result.status, "success")
            self.assertTrue(result.metadata["memory_written"])
            self.assertTrue(system.working_context.read_recent_memory_digest())

    def test_memory_events_write_life_memory_not_raw_mem0(self) -> None:
        class RawMem:
            def __init__(self) -> None:
                self.calls = []

            def add(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        class Backend:
            ready = True

            def __init__(self) -> None:
                self._mem = RawMem()
                self.synced = []

            def store(self, *args, **kwargs):
                self.synced.append((args, kwargs))

        with tempfile.TemporaryDirectory() as tmp:
            backend = Backend()
            system = create_memory_system(character_id="xuezhi", root=tmp, vector_backend=backend)
            store = MemoryEventStore(backend, "xuezhi", memory_system=system)

            store._write_event(StoredEvent(desc="雪吱决定继续研究 Minecraft 铁轨运输。", tags=["minecraft"]))

            self.assertEqual(backend._mem.calls, [])
            self.assertEqual(len(system.store.search("铁轨运输")), 1)
            deadline = time.monotonic() + 2.0
            while not backend.synced and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(backend.synced[0][1]["user_id"], "xuezhi")

    def test_life_runtime_memory_context_prefers_life_memory_system(self) -> None:
        class OldBackend:
            ready = True

            def get_context_multi(self, query, user_ids):
                return "old backend should not be used"

        class FakeLlm:
            def chat(self, messages, options=None):
                return "{}"

        with tempfile.TemporaryDirectory() as tmp:
            system = create_memory_system(character_id="xuezhi", root=tmp, vector_backend=OldBackend())
            system.remember("雪吱把 Minecraft 红石农场作为当前研究主题。")
            session = SimpleNamespace(
                character_id="xuezhi",
                character_name="雪吱",
                character_data={"name": "雪吱"},
                memory_system=system,
                memory_backend=OldBackend(),
            )
            runtime = LifeRuntime(session=session, section={"enabled": False}, llm=FakeLlm(), root=Path(tmp))

            context = runtime._memory_context(
                event_text="继续研究 Minecraft 红石农场",
                inner_stream="",
                digest="",
            )

            self.assertIn("红石农场", context)
            self.assertNotIn("old backend", context)


    def test_memory_lifecycle_sediments_event_log_by_llm_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            def llm_call(messages, options):
                return """
                {
                  "remember": [
                    {
                      "record_form": "open_thread",
                      "content": "雪吱多次搜索 Minecraft 冒险模组设计案例没有直接结果，之后想从玩法机制和社区作品反推设计方向。",
                      "summary": "Minecraft 冒险模组设计研究转向",
                      "importance": "medium",
                      "keywords": ["Minecraft", "冒险模组", "玩法机制"],
                      "tags": ["research"],
                      "source_event_ids": ["evt_search_fail"]
                    }
                  ],
                  "archive": [
                    {"event_id": "evt_wait", "reason": "只是空等待"}
                  ],
                  "notes": "沉淀研究转向，归档空等待"
                }
                """

            system = create_memory_system(character_id="xuezhi", root=tmp, llm_call=llm_call)
            system.append_event(
                MemoryEventDraft(
                    character_id="xuezhi",
                    event_id="evt_search_fail",
                    content="搜索 Minecraft 冒险模组设计案例没有找到直接案例，开始考虑从玩法机制反推。",
                    source="web_search",
                    event_type="tool_result",
                )
            )
            system.append_event(
                MemoryEventDraft(
                    character_id="xuezhi",
                    event_id="evt_wait",
                    content="wait: wait",
                    source="self",
                    event_type="action_result",
                )
            )

            decision = system.sediment_once()

            self.assertEqual(len(decision.remember), 1)
            recalled = system.deep_recall("冒险模组 玩法机制")
            self.assertIn("玩法机制", recalled)
            archive = Path(tmp) / "characters" / "xuezhi" / "memory" / "archive" / "forgotten.jsonl"
            self.assertIn("evt_wait", archive.read_text(encoding="utf-8"))

    def test_memory_lifecycle_worker_processes_appended_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            def llm_call(messages, options):
                return """
                {
                  "remember": [
                    {
                      "record_form": "open_thread",
                      "content": "雪吱研究 Minecraft 冒险模组时，把结构探索、战利品节奏和玩家路线作为接下来的观察线索。",
                      "summary": "Minecraft 冒险模组研究线索",
                      "importance": "medium",
                      "keywords": ["Minecraft", "冒险模组", "结构探索"],
                      "tags": ["research"],
                      "source_event_ids": ["evt_worker_research"]
                    }
                  ],
                  "archive": [],
                  "notes": "沉淀研究线索"
                }
                """

            system = create_memory_system(character_id="xuezhi", root=tmp, llm_call=llm_call)
            try:
                system.start_lifecycle_worker(interval_seconds=60.0, max_batches_per_wake=2)
                system.append_event(
                    MemoryEventDraft(
                        character_id="xuezhi",
                        event_id="evt_worker_research",
                        content="雪吱在研究 Minecraft 冒险模组，开始关注结构探索、战利品节奏和玩家路线。",
                        source="debug_text",
                        event_type="text",
                    )
                )
                deadline = time.monotonic() + 2.0
                recalled = ""
                while time.monotonic() < deadline:
                    recalled = system.deep_recall("冒险模组 结构探索")
                    if "结构探索" in recalled:
                        break
                    time.sleep(0.02)
                self.assertIn("结构探索", recalled)
            finally:
                system.stop_lifecycle_worker()

    def test_memory_lifecycle_archives_events_omitted_by_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            def llm_call(messages, options):
                return '{"remember": [], "archive": [], "notes": "nothing explicit"}'

            system = create_memory_system(character_id="xuezhi", root=tmp, llm_call=llm_call)
            system.append_event(
                MemoryEventDraft(
                    character_id="xuezhi",
                    event_id="evt_omitted",
                    content="雪吱短暂查看了一次无结果的调试输出。",
                    source="debug_text",
                    event_type="text",
                )
            )

            decision = system.sediment_once()

            self.assertEqual(decision.archive[0]["event_id"], "evt_omitted")
            archive = Path(tmp) / "characters" / "xuezhi" / "memory" / "archive" / "forgotten.jsonl"
            self.assertIn("evt_omitted", archive.read_text(encoding="utf-8"))

    def test_chat_session_conversation_turn_is_event_not_immediate_life_memory(self) -> None:
        from kokoro.core import input_events
        from kokoro.core.chat_session import ChatSession

        with tempfile.TemporaryDirectory() as tmp:
            system = create_memory_system(character_id="xuezhi", root=tmp)
            session = ChatSession(
                character_id="xuezhi",
                character_data={"name": "雪吱"},
                memory_backend=SimpleNamespace(ready=False),
                user_name="真冬",
            )
            session.memory_system = system
            session.memory_events = None
            session.cognition = SimpleNamespace(refresh_cache=lambda *args, **kwargs: None)
            session.emotion = SimpleNamespace(evaluate=lambda *args, **kwargs: None)
            session.inner_stream = SimpleNamespace(get_context=lambda: "")
            session.event_bus = SimpleNamespace(publish=lambda event: None)
            session.input_registry = input_events.default_registry()

            session.remember("今天继续看冒险模组。", "嗯，先从玩法机制找线索。", async_store=False)

            self.assertEqual(system.store.recent(limit=5), [])
            events = system.event_log.recent_events(limit=5)
            self.assertTrue(any(event.get("type") == "conversation_turn" for event in events))


if __name__ == "__main__":
    unittest.main()
