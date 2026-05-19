# 提示词系统

## 架构

所有提示词集中在项目根目录的 `prompts.json`，通过 `kokoro/prompts.py` 加载。

```python
prompts.get("dialogue_orchestrator.planner_system")  # 点分路径访问
prompts.format_prompt("impulse.planner_user", name="爱丽丝", ...)  # 带格式化
```

设计意图：所有与 LLM 交互的文本集中管理，不散落在各模块的字符串字面量中。

## 按模块的提示词分类

### 角色主提示词

| key | 用途 |
|---|---|
| `character_system.template` | 角色 system prompt 主模板 |
| `character_system.expression_calibration` | 附加的说话节奏校准 |

模板中使用 `{name}`、`{user_name}`、`{description}`、`{personality}`、`{background}`、`{scene_block}`、`{example_dialogue_block}` 占位。

### 对话调度器

| key | 用途 |
|---|---|
| `dialogue_orchestrator.planner_system` | 话轮判断和主动搭话的 planner 系统提示 |
| `dialogue_orchestrator.planner_user` | planner 用户提示（轮次、角色资料、历史等） |
| `dialogue_orchestrator.generator_context` | 回复生成时的边界指令 |
| `dialogue_orchestrator.reply_character_prompt` | 回复生成用的精简角色提示 |

### 多角色调度器

| key | 用途 |
|---|---|
| `multi_dialogue_orchestrator.planner_system` | 多角色 planner（决定谁说、对谁说） |
| `multi_dialogue_orchestrator.planner_user` | 多角色 planner 用户提示 |
| `multi_dialogue_orchestrator.generator_context` | 多角色生成时的边界指令 |
| `multi_dialogue_orchestrator.reply_character_prompt` | 多角色生成用的精简角色提示 |

### 情绪层

| key | 用途 |
|---|---|
| `emotion.evaluate_system` | 情绪评估系统提示 |
| `emotion.evaluate_user` | 情绪评估用户提示（当前情绪 + 本轮对话） |

### 认知层

| key | 用途 |
|---|---|
| `cognition.evaluate_system` | 认知评估系统提示 |
| `cognition.evaluate_user` | 认知评估用户提示（现有条目 + 对话 + 摘要 + 记忆） |

### 记忆事件

| key | 用途 |
|---|---|
| `memory_events.extract_system` | 事件提取系统提示 |
| `memory_events.extract_user` | 事件提取用户提示 |
| `memory_events.summarize_system` | 事件去重合总结系统提示 |
| `memory_events.summarize_user` | 事件去重合并用户提示 |

### 重叠分类

| key | 用途 |
|---|---|
| `overlap.system` | 重叠分类系统提示 |
| `overlap.user_template` | 重叠分类用户提示 |

### STT 精炼

| key | 用途 |
|---|---|
| `stt_refine.system` | 独立精炼系统提示 |
| `stt_refine.user_template` | 独立精炼用户提示 |
| `stt_refine_inline.system` | inline 模式下注入聊天上下文的纠错提示 |

### 其他

| key | 用途 |
|---|---|
| `impulse.planner_system` / `planner_user` | 旧 impulse planner（兼容保留） |
| `portrait_selection.system` / `user_template` | 立绘表情选择 |
| `conversation_summary.system` / `user_template` | 对话摘要 |
| `memory_importance.user_template` | 记忆重要度判断 |
| `scene.*` | 场景引导文本 |
| `user_commands.*` | 用户命令相关 |
| `tool_calling.*` | 工具调用提示 |
| `vision.*` | 屏幕识别提示 |
| `bilibili_live.*` | 直播场景提示 |

## 设计原则

1. **框架级提示词尽量通用**：不写项目私货，不把具体角色名硬编码进底层规则
2. **事实约束和风格约束分开**：`planner_system` 管事实边界，`generator_context` 管语气
3. **JSON 输出格式**：planner 类提示词要求输出 JSON，`response_format: {"type": "json_object"}`（DeepSeek 兼容模式）
4. **节制长度**：planner 能看到的角色信息控制在 900 字符内，节 token 的同时减少过时上下文干扰

## 缓存考虑

所有 system prompt 是 DeepSeek 前缀缓存的受益者。设计上：
- 稳定前缀（角色 system prompt + history）放在 messages 开头
- 动态内容（记忆、认知、摘要、场景）放在 history 之后
- 这样缓存命中时覆盖尽可能多的 token
