"""User-triggered command detection and execution."""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass
from typing import Iterable

from kokoro import screen_interest
from kokoro import vision

logger = logging.getLogger(__name__)

TYPE_SCREEN_INSPECT = "screen.inspect"


@dataclass(frozen=True)
class UserCommand:
    type: str
    confidence: float
    raw_text: str


@dataclass(frozen=True)
class CommandResult:
    type: str
    ok: bool
    context: str = ""
    screen_context: str = ""
    score: float = 0.0
    private: bool = False
    user_visible_note: str = ""
    error: str = ""


SCREEN_COMMAND_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:帮我|你|麻烦你|可以|能不能|能|给我|替我|请你)?.{0,8}(?:看|看看|瞅|瞧|读|识别|分析|检查|观察|扫|扫一下|看一眼|瞄一眼).{0,18}(?:屏幕|荧幕|画面|桌面|当前窗口|前台窗口|窗口|页面|网页|界面|这里|这儿|这个|这个页面|这个界面|这张图|当前画面|现在画面)",
        r"(?:屏幕|荧幕|画面|桌面|当前窗口|前台窗口|窗口|页面|网页|界面|这里|这儿|这个|当前画面|现在画面).{0,18}(?:有什么|是什么|写了什么|显示什么|显示了什么|在干嘛|哪里不对|怎么回事|帮我看看|帮我分析|你看得见|你能看)",
        r"(?:look|read|scan|inspect|check|analy[sz]e|describe).{0,20}(?:screen|desktop|window|page|browser|this|here)",
        r"(?:what'?s|what is).{0,16}(?:on|in).{0,8}(?:my )?(?:screen|desktop|window|page)",
    )
)

SCREEN_ACTION_RE = re.compile(
    r"(?:看|看看|瞅|瞧|读|识别|分析|检查|观察|扫|看一眼|瞄一眼|look|read|scan|inspect|check|analy[sz]e|describe)",
    re.IGNORECASE,
)
SCREEN_OBJECT_RE = re.compile(
    r"(?:屏幕|荧幕|画面|桌面|当前窗口|前台窗口|窗口|页面|网页|界面|这里|这儿|这个|这张图|当前画面|现在画面|screen|desktop|window|page|browser|this|here)",
    re.IGNORECASE,
)
NEGATIVE_RE = re.compile(
    r"(?:不要|不用|别|无需|不需要|别看|不用看|don't|do not|no need)",
    re.IGNORECASE,
)
NON_COMMAND_RE = re.compile(
    r"(?:我|自己).{0,8}(?:刚才|一直|正在|在).{0,16}(?:看|盯).{0,8}(?:屏幕|荧幕|画面|桌面|页面|网页|界面).{0,12}(?:累|久|半天|一会|一阵|眼睛)",
    re.IGNORECASE,
)


def detect(text: str) -> UserCommand | None:
    normalized = _normalize(text)
    if not normalized or NEGATIVE_RE.search(normalized) or NON_COMMAND_RE.search(normalized):
        return None

    for pattern in SCREEN_COMMAND_PATTERNS:
        if pattern.search(normalized):
            return UserCommand(TYPE_SCREEN_INSPECT, 0.95, text)

    if SCREEN_ACTION_RE.search(normalized) and SCREEN_OBJECT_RE.search(normalized):
        return UserCommand(TYPE_SCREEN_INSPECT, 0.75, text)

    return None


def execute(command: UserCommand, *, timeout: int = 45) -> CommandResult:
    if command.type != TYPE_SCREEN_INSPECT:
        return CommandResult(command.type, False, error=f"unsupported command: {command.type}")

    try:
        foreground = vision.get_foreground_app()
        if screen_interest.foreground_is_private(foreground):
            return CommandResult(
                command.type,
                False,
                context=(
                    "用户刚才要求你查看屏幕，但当前前台窗口疑似包含隐私内容，"
                    "系统已跳过屏幕识别。请简短说明无法查看隐私内容。"
                ),
                private=True,
                user_visible_note="当前窗口可能包含隐私内容，我先不看。",
            )

        content = vision.detect_desktop(prompt=_screen_inspect_prompt(command.raw_text), timeout=timeout).strip()
    except Exception as exc:
        logger.exception("screen command failed")
        return CommandResult(
            command.type,
            False,
            error=f"{type(exc).__name__}: {exc}",
            user_visible_note="我刚才没能成功读取屏幕。",
        )

    if not content:
        content = "屏幕识别没有返回可用内容。"

    return CommandResult(
        command.type,
        True,
        context=format_context(command.raw_text, content),
        screen_context=content,
        score=100.0,
    )


def _screen_inspect_prompt(user_text: str) -> str:
    return (
        "用户主动要求你查看当前屏幕，因此这次任务不是判断是否有趣，也不需要返回 JSON。\n"
        "请用自然中文详细描述当前完整桌面截图和前台窗口内容，重点服务用户刚才的请求。\n"
        f"用户原话：{user_text}\n\n"
        "要求：\n"
        "- 不要输出 JSON、Markdown 代码块或评分。\n"
        "- 优先读取前台窗口里的标题、正文、按钮、错误信息、输入框、列表项和状态提示。\n"
        "- 如果用户像是在问页面哪里不对、下一步怎么做、按钮在哪里，请描述足够信息，方便后续对话模型判断。\n"
        "- 如果看不清某些文字，请明确说看不清，不要编造。\n"
    )


def format_context(user_text: str, screen_content: str) -> str:
    return (
        "用户刚才明确要求你查看屏幕。以下是本次屏幕识别结果：\n"
        f"{screen_content}\n\n"
        f"用户原话：{user_text}\n"
        "请结合用户原话和屏幕内容继续对话。不要只复述屏幕内容；"
        "如果用户是在询问页面、错误、按钮、状态或下一步操作，请直接给出判断和建议。"
    )


def build_waiting_reply(
    user_text: str,
    recent_messages: Iterable[dict],
    *,
    llm_url: str,
    llm_model: str,
    api_key: str | None = None,
    timeout: int = 20,
) -> str:
    recent_text = _format_recent(recent_messages)
    messages = [
        {
            "role": "system",
            "content": (
                "你正在扮演用户的桌面伙伴。用户刚刚发出了需要你查看屏幕的指令，"
                "但屏幕识别还需要等待。请只回复一句自然的等待式回应，表示你马上查看。"
                "要结合最近上下文和用户语气，中文为主，不要解释系统机制，不要提到模型，"
                "不要假装已经看到了屏幕内容。长度控制在 6-18 个汉字。"
            ),
        },
        {
            "role": "user",
            "content": f"最近上下文：\n{recent_text}\n\n用户刚才说：{user_text}\n\n等待式回应：",
        },
    ]
    reply = _call_refine_style_llm(
        messages,
        llm_url=llm_url,
        llm_model=llm_model,
        api_key=api_key,
        timeout=timeout,
    )
    return _clean_waiting_reply(reply) or "好，我看一下。"


def _call_refine_style_llm(
    messages: list[dict],
    *,
    llm_url: str,
    llm_model: str,
    api_key: str | None,
    timeout: int,
) -> str:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        url = f"{llm_url.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": llm_model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 64,
            "thinking": {"type": "disabled"},
        }
    else:
        url = f"{llm_url.rstrip('/')}/api/chat"
        payload = {
            "model": llm_model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": 64},
        }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read())
    if api_key:
        return result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    return result.get("message", {}).get("content", "").strip()


def _format_recent(messages: Iterable[dict], limit: int = 6) -> str:
    tail = list(messages)[-limit:]
    lines: list[str] = []
    for msg in tail:
        role = msg.get("role", "")
        content = str(msg.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content[:160]}")
    return "\n".join(lines) if lines else "无"


def _clean_waiting_reply(text: str) -> str:
    text = text.strip().strip("\"'“”‘’")
    text = re.sub(r"^(?:等待式回应|回复|答复)[:：]\s*", "", text)
    text = re.sub(r"\s+", "", text)
    return text[:40]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())
