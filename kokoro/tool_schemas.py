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

ALL_TOOLS: list[dict] = [
    LOOK_AT_SCREEN,
    SEARCH_MEMORY,
    GET_CURRENT_TIME,
    GET_CURRENT_APP,
    SAVE_TO_MEMORY,
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
}
