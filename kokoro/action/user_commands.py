"""User-triggered command detection and execution."""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass
from typing import Iterable

from kokoro.core import prompts
from kokoro.action import screen_interest
from kokoro.core import token_usage
from kokoro.action import vision
from kokoro.core import config as cfg

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
        r"(?:看|看看|瞅|瞧|读|识别|分析|检查|观察).{0,12}(?:我|我的|咱|咱的).{0,6}(?:屏幕|荧幕|画面|桌面|窗口|页面|界面)",
        r"(?:屏幕|荧幕|画面|桌面|当前窗口|前台窗口|窗口|页面|网页|界面|这里|这儿|这个|当前画面|现在画面).{0,18}(?:有什么|是什么|写了什么|显示什么|显示了什么|在干嘛|哪里不对|怎么回事|帮我看看|帮我分析|你看得见|你能看)",
        r"(?:屏幕|荧幕|画面|桌面|窗口|页面|界面).{0,30}(?:技能|招式|用什么|怎么打|下一步|怎么办|怎么回|怎么回复)",
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
                context=prompts.get("user_commands.privacy_context", ""),
                private=True,
                user_visible_note=prompts.get("user_commands.privacy_note", ""),
            )

        content = vision.detect_desktop(
            prompt=_screen_inspect_prompt(command.raw_text),
            timeout=timeout,
        ).strip()
    except Exception as exc:
        logger.exception("screen command failed")
        return CommandResult(
            command.type,
            False,
            error=f"{type(exc).__name__}: {exc}",
            user_visible_note=prompts.get("user_commands.screen_error_note", ""),
        )

    if not content:
        content = prompts.get("user_commands.empty_screen_content", "")

    return CommandResult(
        command.type,
        True,
        context=format_context(command.raw_text, content),
        screen_context=content,
        score=100.0,
    )


def _screen_inspect_prompt(user_text: str) -> str:
    return prompts.format_prompt("user_commands.screen_inspect_prompt", user_text=user_text)


def format_context(user_text: str, screen_content: str) -> str:
    return prompts.format_prompt(
        "user_commands.screen_result_context",
        user_text=user_text,
        screen_content=screen_content,
    )


def build_waiting_reply(
    user_text: str,
    recent_messages: Iterable[dict],
    *,
    llm_url: str,
    llm_model: str,
    character_name: str = "",
    character_prompt: str = "",
    api_key: str | None = None,
    timeout: int = 20,
) -> str:
    recent_text = _format_recent(recent_messages)
    if character_prompt.strip():
        character_text = character_prompt.strip()
    elif character_name:
        character_text = prompts.format_prompt("user_commands.character_fallback", character_name=character_name)
    else:
        character_text = prompts.get("user_commands.no_context_placeholder", "")
    messages = [
        {
            "role": "system",
            "content": prompts.get("user_commands.waiting_system", ""),
        },
        {
            "role": "user",
            "content": prompts.format_prompt(
                "user_commands.waiting_user",
                character_name=character_name or prompts.get("user_commands.unknown_name", ""),
                character_text=character_text,
                recent_text=recent_text,
                user_text=user_text,
                user_name=cfg_mod.user_name(),
            ),
        },
    ]
    reply = _call_refine_style_llm(
        messages,
        llm_url=llm_url,
        llm_model=llm_model,
        api_key=api_key,
        timeout=timeout,
    )
    return _clean_waiting_reply(reply) or prompts.get("user_commands.waiting_fallback", "")


def _call_refine_style_llm(
    messages: list[dict],
    *,
    llm_url: str,
    llm_model: str,
    api_key: str | None,
    timeout: int,
) -> str:
    from kokoro.core import deepseek_api

    return deepseek_api.chat(
        messages,
        model=llm_model,
        temperature=0.7,
        max_tokens=64,
        function="waiting_reply",
        timeout=timeout,
    )["content"]


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
