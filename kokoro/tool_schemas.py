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

SEND_QQ_MESSAGE = {
    "type": "function",
    "function": {
        "name": "send_qq_message",
        "description": (
            "通过已连接的 QQ 通道，以角色本人身份发送一条消息。"
            "仅当角色自己判断当前场景适合这样做时使用，例如她想参与 QQ、回应 QQ 上下文，"
            "或完成一个 QQ 侧社交动作。不要用于普通口头回复。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "要准确发送到 QQ 的文本，不要包含动作描写。",
                },
                "conversation_id": {
                    "type": "string",
                    "description": "可选目标，例如 group:123 或 private:456。留空则使用最近的 QQ 会话。",
                },
                "reason": {
                    "type": "string",
                    "description": "简短内部理由，说明为什么现在发送是自然的。",
                },
            },
            "required": ["message"],
        },
    },
}

RETIRE_STICKER = {
    "type": "function",
    "function": {
        "name": "retire_sticker",
        "description": (
            "主动停用一张本地表情包。仅当角色自己根据长期记忆、他人反馈或当前社交判断，"
            "认为某张表情包以后不该再用、容易冒犯、语境不合适，或想认真回应对方要求时使用。"
            "这不是外部硬限制，而是角色自己的整理行为。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sticker_id": {
                    "type": "string",
                    "description": "要停用的表情包 id。",
                },
                "reason": {
                    "type": "string",
                    "description": "简短说明为什么决定停用。",
                },
            },
            "required": ["sticker_id"],
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
                        "pout", "sigh", "cry", "doubt", "confused", "awkward", "neutral",
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

VTS_MOTION = {
    "type": "function",
    "function": {
        "name": "vts_motion",
        "description": (
            "控制角色的 Live2D 身体和头部动作。用户要求你笑一下、摇头晃脑、点头、测试身体、"
            "让皮套动起来，或你自己想用身体动作表达情绪时使用。这个工具是真实控制 Live2D，"
            "不是后台代码任务。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "motion": {
                    "type": "string",
                    "enum": [
                        "smile", "happy", "nod", "shake", "sway", "bounce",
                        "excited", "shy", "pout", "sad", "thinking", "idle",
                    ],
                    "description": "动作类型。shake/sway 适合摇头晃脑，bounce/excited 适合活泼身体动作。",
                },
                "intensity": {
                    "type": "number",
                    "description": "动作强度 0.0-1.0，测试或明显表达时可用 0.7-1.0。",
                    "minimum": 0,
                    "maximum": 1,
                },
                "duration_seconds": {
                    "type": "number",
                    "description": "动作持续秒数，建议 2-6 秒。",
                    "minimum": 0.5,
                    "maximum": 12,
                },
                "reason": {
                    "type": "string",
                    "description": "简短内部理由，说明为什么现在这样动。",
                },
            },
            "required": ["motion"],
        },
    },
}

CLAUDE_CODE_EXEC = {
    "type": "function",
    "function": {
        "name": "claude_code_exec",
        "description": "调用智能体（Claude Code）执行文件操作、代码编写、文本处理、搜索分析等"
                       "需要计算机操作的任务。当你意识到用户需要读写文件、整理笔记、搜索或修改代码、"
                       "或者任何需要操作计算机来完成的事情时使用。调用前先向用户确认你要做什么。"
                       "任务会在后台执行，你可以用 check_task_progress 查询进度。",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "要完成的目标描述，越清晰越好。包含目标、要写入的内容、文件路径、格式要求等具体信息。",
                },
                "working_dir": {
                    "type": "string",
                    "description": "工作目录路径，留空使用项目根目录。",
                },
            },
            "required": ["task"],
        },
    },
}

CHECK_TASK_PROGRESS = {
    "type": "function",
    "function": {
        "name": "check_task_progress",
        "description": "查询正在进行的智能体任务的最新状态和进度。当用户问「好了吗」「还没好吗」「进度如何」"
                       "时使用。也用于你自己主动检查长时间运行的task是否已完成。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务的ID（创建任务时返回的8位ID）。留空则返回所有活跃任务。",
                },
            },
            "required": [],
        },
    },
}

LIST_ACTIVE_TASKS = {
    "type": "function",
    "function": {
        "name": "list_active_tasks",
        "description": "列出当前所有活跃（进行中、等待中）的智能体任务。当你想了解自己还有哪些任务在"
                       "后台运行时使用。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

CANCEL_TASK = {
    "type": "function",
    "function": {
        "name": "cancel_task",
        "description": "取消一个正在运行或等待中的智能体任务。当用户说「不用了」「取消吧」或者你判断该"
                       "任务已不再需要时使用。已完成的或已失败的任务不能取消。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "要取消的任务ID。",
                },
            },
            "required": ["task_id"],
        },
    },
}

ALL_TOOLS: list[dict] = [
    LOOK_AT_SCREEN,
    SEARCH_MEMORY,
    GET_CURRENT_TIME,
    GET_CURRENT_APP,
    SAVE_TO_MEMORY,
    SEND_QQ_MESSAGE,
    RETIRE_STICKER,
    VTS_EXPRESSION,
    VTS_MOTION,
    CLAUDE_CODE_EXEC,
    CHECK_TASK_PROGRESS,
    LIST_ACTIVE_TASKS,
    CANCEL_TASK,
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
    "send_qq_message",
    "retire_sticker",
    "vts_expression",
    "vts_motion",
    "claude_code_exec",
    "check_task_progress",
    "list_active_tasks",
    "cancel_task",
}
