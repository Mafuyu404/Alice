import time
import sys
import types

for _name in ("kokoro.stt", "kokoro.pool", "kokoro.tts", "kokoro.memory", "kokoro.vision"):
    sys.modules.setdefault(_name, types.ModuleType(_name))

from kokoro.action import Action, ActionBatch, ActionRuntime
from kokoro.action.autonomous_step import AutonomousStep
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


def test_action_runtime_shutdown_flushes_pending_results():
    session = _Session()
    runtime = ActionRuntime(
        session=session,
        merge_window_seconds=60.0,
        handlers={"tool": lambda action: "pending result"},
    )
    batch = ActionBatch(
        cycle_id="cycle_shutdown",
        causality_id="cause_shutdown",
        actions=[Action(action="tool")],
    )

    runtime.execute_batch(batch)
    assert not [event for event in session.event_bus.snapshot() if event.type == "action_result"]
    runtime.shutdown(wait=False)
    result_events = [event for event in session.event_bus.snapshot() if event.type == "action_result"]

    assert len(result_events) == 1
    assert result_events[0].content == "pending result"


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


def test_suppressed_record_only_result_does_not_feed_back():
    session = _Session()
    runtime = ActionRuntime(
        session=session,
        merge_window_seconds=0,
        handlers={"wait": lambda action: "waiting"},
    )
    batch = ActionBatch(
        cycle_id="cycle_wait",
        causality_id="cause_wait",
        actions=[
            Action(
                action="wait",
                args={"suppress_feedback": True},
                result_policy="record_only",
            )
        ],
    )

    runtime.execute_batch(batch)
    events = session.event_bus.snapshot()

    assert not [event for event in events if event.type == "action_result"]


def test_autonomous_wait_defaults_to_record_only_suppressed():
    step = object.__new__(AutonomousStep)
    action = step._normalize_wait_action(Action(action="wait", result_policy="feed_back"))

    assert action.result_policy == "record_only"
    assert action.args["suppress_feedback"] is True
