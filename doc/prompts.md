# 提示词管理

## 概述

所有 LLM 提示词集中在 `prompts.json` 中，由 `kokoro/prompts.py` 加载和格式化。集中管理便于统一修改和维护。

## 加载方式

```python
from kokoro import prompts

# 获取纯文本（点号路径访问）
content = prompts.get("section.key")

# 获取纯文本（带默认值）
content = prompts.get("section.key", "fallback")

# 格式化（Python str.format）
content = prompts.format_prompt("section.key", var1=value1, var2=value2)
```

- `load()` 首次调用时读取 `prompts.json` 并缓存
- `get(path, default)` 支持点号分隔的多级路径，如 `"character_system.template"`
- `format_prompt(path, **values)` 对模板字符串调用 `.format(**values)`

## 提示词结构

### 角色系统

| 键 | 用途 |
|------|------|
| `character_system.template` | 角色设定模板（含对话守则 + 格式要求）。参数：`{name}` `{description}` `{personality}` `{background}` `{relationship}` `{background_block}` `{relationship_block}` `{example_dialogue_block}` |
| `character_system.expression_calibration` | 表达校准规则。拼接在 template 之后，约束语音对话的写实性、回答风格 |

### 对话会话

| 键 | 用途 |
|------|------|
| `chat_session.screen_context_prefix` | 屏幕上下文前缀，用于格式化最近屏幕观察记录列表 |

### STT 精炼

| 键 | 用途 |
|------|------|
| `stt_refine.system` | 精炼系统提示词（separate 模式使用） |
| `stt_refine.user_template` | 精炼用户提示模板。参数：`{text}` |
| `stt_refine_inline.system` | inline 模式注入聊天消息的纠错提示词 |

### 记忆

| 键 | 用途 |
|------|------|
| `memory_importance.system` | （已废弃，不再直接使用 system prompt） |
| `memory_importance.user_template` | 记忆重要度判断。参数：`{user_msg}` `{assistant_msg}` |

### 立绘选择

| 键 | 用途 |
|------|------|
| `portrait_selection.system` | 立绘选择系统提示词 |
| `portrait_selection.user_template` | 选择请求模板。参数：`{current_id}` `{user_text}` `{assistant_text}` `{time_info}` `{catalog}` |
| `portrait_selection.time_info_idle` | 空闲时间信息模板。参数：`{seconds}` |
| `portrait_selection.time_info_recent` | 对话刚结束的时间信息 |

### 主动搭话

| 键 | 用途 |
|------|------|
| `proactive.idle` | IDLE 主动搭话的角色提示 |
| `proactive.recent` | RECENT 主动搭话的角色提示 |
| `proactive.mem` | MEM 主动搭话的角色提示 |
| `proactive.screen` | SCREEN 主动搭话的角色提示 |
| `proactive.screen_context_label` | SCREEN 上下文标签。参数：`{context}` |
| `proactive.mem_context_label` | MEM 上下文标签。参数：`{context}` |
| `proactive.trigger_system` | 主动搭话触发时的 system 提示词后缀 |
| `proactive.trigger_guidance_label` | 角色特定指导标签。参数：`{guidance}` |

### 屏幕兴趣

| 键 | 用途 |
|------|------|
| `screen_interest.content_analysis` | 屏幕截图分析提示词。参数：`{fg_info}`（前台窗口标题+进程名） |

### 记忆事件

| 键 | 用途 |
|------|------|
| `memory_events.memory_lookup` | 记忆事件上下文提示。参数：`{context}` |

### 用户命令

| 键 | 用途 |
|------|------|
| `user_commands.screen_inspect_prompt` | 用户主动要求看屏幕时的分析提示。参数：`{user_text}` |
| `user_commands.screen_result_context` | 屏幕指令执行后的上下文注入模板。参数：`{screen_content}` `{user_text}` |
| `user_commands.waiting_system` | 等待屏幕识别时的等待回应系统提示 |
| `user_commands.waiting_user` | 等待回应用户模板。参数：`{character_name}` `{character_text}` `{recent_text}` `{user_text}` |
| `user_commands.waiting_fallback` | 等待回应的保底回复（LLM 调用失败时使用） |
| `user_commands.privacy_context` | 隐私内容时的对话上下文 |
| `user_commands.privacy_note` | 隐私内容时的用户可见提示 |
| `user_commands.screen_error_note` | 屏幕识别失败的提示 |
| `user_commands.empty_screen_content` | 屏幕识别结果为空时的内容 |
| `user_commands.character_fallback` | 角色名兜底模板。参数：`{character_name}` |
| `user_commands.no_context_placeholder` | 无上下文时的占位文本 |
| `user_commands.unknown_name` | 未知角色名占位 |

### 视觉

| 键 | 用途 |
|------|------|
| `vision.analyze_suffix` | 视觉分析后缀（附加运行窗口列表信息）。参数：`{app_text}` |

## 提示词注入流程

### 正常对话
```
system: character_system.template + expression_calibration
system: (extra_context — 指令结果等)
system: chat_session.screen_context_prefix + 编号列表
system: 记忆上下文（从 memory_backend.get_context 获取）
history...
user: 当前输入
```

### 正常对话 (stt_refine_inline=True)
```
system: character_system.template + expression_calibration
system: (extra_context)
system: 屏幕上下文
system: 记忆上下文
history...
system: stt_refine_inline.system
user: 当前输入（经 local_clean_stt 清洗）
```

### 主动搭话
```
system: character_system.template + expression_calibration
system: proactive.trigger_system + proactive.trigger_guidance_label
system: proactive.{behavior}
system: proactive.{screen/mem}_context_label（含 context）
system: 最近屏幕观察记录
system: 记忆上下文
history...
user: 内部触发指令
```

### 用户屏幕命令
```
system: character_system.template + expression_calibration
system: user_commands.screen_result_context（含 screen_content + user_text）
system: 记忆上下文
history...
user: 用户原话
```
