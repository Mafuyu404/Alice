from kokoro.action import model as action_model
from kokoro.action import tool_spec
from kokoro.action.tools.send_qq_message.prepare import prepare_message


class _LLM:
    def __init__(self, content='{"allow": true, "block_type": "none", "reason": "ok"}'):
        self.content = content
        self.calls = []

    def chat(self, messages, options=None):
        self.calls.append((messages, dict(options or {})))
        return self.content


class _Runtime:
    def __init__(self, llm):
        self.llm = llm


class _Session:
    def __init__(self, llm):
        self.life_runtime = _Runtime(llm)


def _ctx(llm=None, **data):
    return tool_spec.ToolContext(
        session=_Session(llm or _LLM()),
        data={
            "recent_qq_conversation_id": "private:10001",
            "recent_qq_event_batch": "真冬: 喂喂喂",
            **data,
        },
    )


def test_normal_private_message_prepare_does_not_call_llm_audit():
    llm = _LLM()
    action = action_model.Action(
        action="send_qq_message",
        args={"message": "你刚刚喊我了？我在。"},
    )

    prepared = prepare_message(_ctx(llm), action)

    assert prepared.args["message"] == "你刚刚喊我了？我在。"
    assert prepared.args["conversation_id"] == "private:10001"
    assert prepared.metadata["blocked"] is False
    assert llm.calls == []


def test_prepare_corrects_requested_conversation_to_recent_context():
    action = action_model.Action(
        action="send_qq_message",
        args={"message": "我在这边回你。", "conversation_id": "group:wrong"},
    )

    prepared = prepare_message(_ctx(), action)

    assert prepared.args["conversation_id"] == "private:10001"
    assert prepared.metadata["blocked"] is False


def test_prepare_blocks_obvious_tool_payload_without_llm():
    llm = _LLM()
    action = action_model.Action(
        action="send_qq_message",
        args={"message": '{"action":"send_qq_message","args":{"message":"hi"}}'},
    )

    prepared = prepare_message(_ctx(llm), action)

    assert prepared.args["message"] == ""
    assert prepared.metadata["blocked"] is True
    assert prepared.metadata["audit"]["block_type"] == "assistant_style"
    assert llm.calls == []


def test_risky_message_audit_bypasses_priority_queue():
    llm = _LLM()
    action = action_model.Action(
        action="send_qq_message",
        args={"message": "根据搜索结果，你昨天说的那个设定我查到了。"},
    )

    prepared = prepare_message(_ctx(llm), action)

    assert prepared.metadata["blocked"] is False
    assert len(llm.calls) == 1
    options = llm.calls[0][1]
    assert options["function"] == "send_qq_message_audit"
    assert options["bypass_priority_queue"] is True
    assert options["priority"] == 0
    assert options["timeout"] <= 5
