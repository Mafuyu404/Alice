"""QQ input data models."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class QQRawMessage:
    message_type: str
    content: str
    user_id: str
    nickname: str
    group_id: str = ""
    group_name: str = ""
    message_id: str = ""
    timestamp: float = field(default_factory=time.time)
    self_id: str = ""
    conversation_id_override: str = ""

    @property
    def conversation_id(self) -> str:
        if self.conversation_id_override:
            return self.conversation_id_override
        if self.message_type == "group" and self.group_id:
            return f"group:{self.group_id}"
        return f"private:{self.user_id}"

    @property
    def conversation_label(self) -> str:
        if self.message_type == "group":
            name = self.group_name or self.group_id or "unknown"
            return f"QQ群 {name}"
        return f"QQ私聊 {self.nickname or self.user_id}"

    def prompt_line(self) -> str:
        clock = datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")
        speaker = self.nickname or self.user_id or "unknown"
        return f"[{clock}] {speaker}: {self.content}"


@dataclass
class QQContextPacket:
    conversation_id: str
    message_type: str
    label: str
    lines: list[str]
    participant_names: list[str]
    started_at: float
    ended_at: float
    unread_count: int
    attention_lines: list[str] = field(default_factory=list)
    relation_lines: list[str] = field(default_factory=list)
    idle_probe: bool = False
    self_message_count: int = 0
    recent_self_lines: list[str] = field(default_factory=list)
    turn_key: str = ""
    memory_context: str = ""
    recall_anchors: list[str] = field(default_factory=list)

    @property
    def content(self) -> str:
        participants = "、".join(self.participant_names[:20]) or "无"
        duration = max(0.0, self.ended_at - self.started_at)
        now = time.time()
        current_time = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
        start_time = datetime.fromtimestamp(self.started_at).strftime("%Y-%m-%d %H:%M:%S")
        end_time = datetime.fromtimestamp(self.ended_at).strftime("%Y-%m-%d %H:%M:%S")
        since_latest = max(0.0, now - self.ended_at)
        scene_note = (
            "这是当前QQ群现场的一部分，群友的发言可以自然成为当下社交输入。"
            if self.message_type == "group"
            else "这是当前QQ私聊现场的一部分；这里的发言来自正在和角色直接说话的人。"
        )
        return (
            f"【QQ环境】\n"
            f"位置：{self.label}\n"
            f"现场说明：{scene_note}\n"
            f"当前时间：{current_time}\n"
            f"窗口开始：{start_time}\n"
            f"窗口最新：{end_time}\n"
            f"时间跨度：约 {duration:.0f} 秒\n"
            f"距最新消息：约 {since_latest:.0f} 秒\n"
            f"消息数：{self.unread_count}\n"
            f"空闲探测：{'是' if self.idle_probe else '否'}\n"
            f"参与者：{participants}\n"
            f"社交信号：{self.attention_summary}\n\n"
            f"话轮关系：{self.relation_summary}\n\n"
            f"自身发言态势：{self.self_activity_summary}\n\n"
            f"最近消息：\n" + "\n".join(self.lines)
        ).strip()

    @property
    def attention_summary(self) -> str:
        if not self.attention_lines:
            return "没有明显点名，但仍是当前社交现场；可以继续旁听，也可以自然想起一个轻量话题。" if self.idle_probe else "没有明显点名，但仍是当前社交现场。"
        return "；".join(self.attention_lines[:8])

    @property
    def relation_summary(self) -> str:
        if not self.relation_lines:
            return "未发现明确指向本角色的话轮；默认按旁听现场理解，不把其他人之间的建议当成自己的任务。"
        return "；".join(self.relation_lines[:8])

    @property
    def self_activity_summary(self) -> str:
        if self.self_message_count <= 0:
            return "最近没有连续自发言。"
        lines = "；".join(self.recent_self_lines[-3:])
        if self.self_message_count >= 3:
            return f"最近自己已经连续/密集说了 {self.self_message_count} 次，需要留意是否在追问同一件事过久。最近：{lines}"
        return f"最近自己说了 {self.self_message_count} 次。最近：{lines}"
