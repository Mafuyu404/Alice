# 提示词管理

## 概述

所有 LLM 提示词集中在 `prompts.json` 中，由 `kokoro/prompts.py` 加载和格式化。集中管理便于修改和维护。

## 加载方式

```python
from kokoro import prompts
content = prompts.get("section.key")
content = prompts.format_prompt("section.key", var1=value1, var2=value2)
```

## 提示词结构

| 键 | 用途 |
|------|------|
| `character_system.template` | 角色设定模板，被 build_system_prompt() 使用 |
| `stt_refine.system` | STT 精炼系统提示词 |
| `stt_refine.user_template` | STT 精炼用户提示模板（参数：`{text}`） |
| `memory_importance.system` | 判断对话是否值得记忆 |
| `memory_importance.user` | 记忆重要度判断用户提示词 |
| `portrait_selection.system` | 立绘选择系统提示词 |
| `portrait_selection.time_info_idle` | 空闲时的时间信息 |
| `portrait_selection.time_info_recent` | 对话刚结束时的沉浸式时间信息 |
| `proactive.idle` | IDLE 主动搭话提示词 |
| `proactive.recent` | RECENT 主动搭话提示词 |
| `proactive.mem` | MEM 主动搭话提示词 |
| `proactive.screen` | SCREEN 主动搭话提示词 |
| `proactive.trigger_system` | 主动搭话触发时的 system 提示词后缀 |
| `screen_interest.content_analysis` | 屏幕兴趣分析（参数：`{fg_info}`） |
| `memory_events.memory_lookup` | 记忆事件上下文提示词（参数：`{context}`） |
| `vision.analyze_suffix` | 视觉分析后缀 |

## 提示词注入机制

### 主动搭话的提示词注入

`prompts.json` 中的 `proactive.*` 提示词包含 `{name}` 和 `{relationship}` 占位符（角色名和称呼），`prompts.format_prompt()` 填充后注入为 system 提示词。

`proactive.trigger_system` 在每个主动搭话轮次作为 system 提示词后缀注入，提醒 AI 这是主动行为。

### 角色特定指导

`characters.json` 中的 `proactive_guidance` 字段在 `cli.py` 中以运行时追加的方式注入到主动搭话的 system 提示词中：

```python
trigger_prompt = prompts.get("proactive.trigger_system")
if char_data.get("proactive_guidance"):
    trigger_prompt += "\n\n" + char_data["proactive_guidance"]
```

### 模板格式

使用 Python `str.format()` 语法，支持命名占位符：
```
"请分析以下屏幕内容的基础信息，不要过度解读：\n{fg_info}"
```
