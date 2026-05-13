"""OpenAI-compatible tool (function calling) JSON schemas."""

from __future__ import annotations

LOOK_AT_SCREEN = {
    "type": "function",
    "function": {
        "name": "look_at_screen",
        "description": "截取用户当前屏幕截图并进行分析。当用户明确要求查看屏幕、读取屏幕内容、分析当前页面/窗口时使用。也适用于用户问'这里有什么'、'帮我看看这个'等指代屏幕的请求。",
        "parameters": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": "可选的关注点或具体问题，例如'错误信息是什么'、'这个页面哪里不对'。留空则做整体描述。",
                },
            },
            "required": [],
        },
    },
}

SEARCH_MEMORY = {
    "type": "function",
    "function": {
        "name": "search_memory",
        "description": "搜索与当前对话相关的过往记忆。当用户提到之前聊过的话题、问你'还记得吗'、或者过往上下文可能有助于更好回答时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "用用户语言描述要搜索的关键信息，例如'用户之前提到的项目名称'、'用户的偏好设置'。",
                },
            },
            "required": ["query"],
        },
    },
}

GET_CURRENT_TIME = {
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "获取当前日期和时间。当用户问现在几点、今天几号、星期几，或需要时间上下文来回应时使用。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

GET_CURRENT_APP = {
    "type": "function",
    "function": {
        "name": "get_current_app",
        "description": "获取用户当前前台窗口的应用名称和进程信息（不截屏、不读取内容）。用于了解用户正在使用什么软件。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

SAVE_TO_MEMORY = {
    "type": "function",
    "function": {
        "name": "save_to_memory",
        "description": "将重要信息保存到长期记忆中。当用户明确要求你记住某事，或当对话中出现了值得保留的重要信息（用户偏好、计划、重要事实）时主动使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要记住的信息，用简洁的一句话概括。",
                },
                "importance": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "记忆重要程度。high=用户明确要求记住的关键信息，medium=对话中自然出现的偏好或计划，low=可能有用的细节。",
                },
            },
            "required": ["content"],
        },
    },
}

VTS_EXPRESSION = {
    "type": "function",
    "function": {
        "name": "vts_expression",
        "description": "控制角色的Live2D面部表情。在你说的话需要配合特定表情时使用，例如微笑、挑眉、撇嘴、叹气、眨眼等。日常对话中的表情由情绪系统自动处理，你只需要在觉得此处应该有一个特定表情来强调语气时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "enum": [
                        "smile", "happy", "sad", "angry", "surprised",
                        "tired", "thinking", "shy", "excited", "wink",
                        "pout", "sigh", "cry", "doubt", "awkward", "neutral",
                    ],
                    "description": "要展示的表情。不调用时情绪系统会自动处理，这里只用于刻意强调。",
                },
                "intensity": {
                    "type": "number",
                    "description": "表情强度 0.0-1.0，1.0为全强度",
                    "minimum": 0,
                    "maximum": 1,
                },
                "duration_seconds": {
                    "type": "number",
                    "description": "表情持续秒数。0=持续到被下一个表情覆盖（默认），正数=秒后自动恢复",
                    "minimum": 0,
                    "maximum": 30,
                },
            },
            "required": ["expression"],
        },
    },
}

ALL_TOOLS: list[dict] = [
    LOOK_AT_SCREEN,
    SEARCH_MEMORY,
    GET_CURRENT_TIME,
    GET_CURRENT_APP,
    SAVE_TO_MEMORY,
    VTS_EXPRESSION,
]

ALL_TOOLS_BY_NAME: dict[str, dict] = {
    t["function"]["name"]: t for t in ALL_TOOLS
}

DEFAULT_ENABLED_TOOLS: set[str] = {
    "look_at_screen",
    "search_memory",
    "get_current_time",
    "get_current_app",
    "save_to_memory",
    "vts_expression",
}
