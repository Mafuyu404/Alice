import time
import sys
import types

for _name in ("kokoro.stt", "kokoro.pool", "kokoro.tts", "kokoro.memory", "kokoro.vision"):
    sys.modules.setdefault(_name, types.ModuleType(_name))

from kokoro.action import Action, ActionBatch, ActionRuntime
from kokoro.core import input_events


class _Session:
    def __init__(self):
        self.event_bus = input_events.InputEventBus()
        self.self_actions = []

    def record_self_action(self, content, *, source="self", action="", metadata=None, lifetime="session"):
        event = input_events.build_self_action_event(
            content,
            source=source,
            action=action,
            metadata=metadata or {},
            lifetime=lifetime,
        )
        self.self_actions.append(event)
        self.event_bus.publish(event)
        return event


def test_action_batch_parses_multiple_actions():
    batch = ActionBatch.from_dict(
        {
            "cycle_id": "cycle_x",
            "causality_id": "cause_y",
            "actions": [
                {"action": "wait", "args": {"reason": "quiet"}},
                {"action": "search_web", "mode": "async", "args": {"query": "abc"}},
            ],
        }
    )

    assert batch.cycle_id == "cycle_x"
    assert batch.causality_id == "cause_y"
    assert [action.action for action in batch.actions] == ["wait", "search_web"]
    assert batch.actions[1].mode == "async"


def test_action_runtime_merges_parallel_results():
    session = _Session()
    runtime = ActionRuntime(
        session=session,
        merge_window_seconds=0.05,
        handlers={
            "a": lambda action: "result a",
            "b": lambda action: "result b",
        },
    )
    batch = ActionBatch(
        cycle_id="cycle_x",
        causality_id="cause_y",
        actions=[
            Action(action="a", mode="async"),
            Action(action="b", mode="async"),
        ],
    )

    runtime.execute_batch(batch)
    time.sleep(0.25)
    events = session.event_bus.snapshot()
    result_events = [event for event in events if event.type == "action_result"]

    assert len(result_events) == 1
    event = result_events[0]
    assert event.source == "action_batch"
    assert event.metadata["cycle_id"] == "cycle_x"
    assert event.metadata["causality_id"] == "cause_y"
    assert event.metadata["merged_count"] == 2
    assert "result a" in event.content
    assert "result b" in event.content


def test_public_action_started_is_recorded():
    session = _Session()
    runtime = ActionRuntime(
        session=session,
        merge_window_seconds=0,
        handlers={"say": lambda action: "said"},
    )
    batch = ActionBatch(
        cycle_id="cycle_public",
        causality_id="cause_public",
        actions=[Action(action="say", visibility="public")],
    )

    runtime.execute_batch(batch)
    events = session.event_bus.snapshot()

    assert any(event.type == "self_action" and event.metadata.get("status") == "started" for event in events)
    assert any(event.type == "action_result" and event.metadata.get("status") == "success" for event in events)
