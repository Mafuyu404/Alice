# 提示词

全局提示词模板位于 `prompts.json`。代码通过 `kokoro/prompts.py` 读取，支持点路径访问：

```python
prompts.get("impulse.planner_user")
prompts.format_prompt("conversation_summary.user_template", conversation="...")
```

## 主要分组

| 分组 | 用途 |
| --- | --- |
| `character_system` | 角色 system prompt 模板 |
| `chat_session` | 对话上下文前缀 |
| `stt_refine` | STT 独立精炼 |
| `stt_refine_inline` | 聊天内隐式纠错 |
| `memory_importance` | 记忆重要性判断 |
| `conversation_summary` | 对话摘要 |
| `portrait_selection` | 立绘选择 |
| `screen_interest` | 屏幕兴趣分析 |
| `memory_events` | 记忆事件触发 |
| `impulse` | 主动搭话 planner 和 trigger |
| `dialogue_orchestrator` | 一对一自然对话调度、第三人称发言约束、屏幕/网页缓存使用决策 |
| `bilibili_live` | 直播模式提示 |
| `tool_calling` | 工具调用结果和错误提示 |
| `tool_handlers` | 工具处理器默认文本 |
| `vision` | 视觉识别默认提示和桌面分析后缀 |
| `overlap` | 用户插话时的重叠判断 |
| `multi_dialogue_orchestrator` | 多角色对话调度、speaker 选择和多人发言约束 |
| `scene` | 场景提示 |

## 人格迭代建议

优先迭代这些文件：

- `characters/{id}/{id}.json`
- `characters/{id}/config.toml`
- `prompts.json`
- `characters/{id}/cognition.json`
- `characters/{id}/emotion.txt`

使用 `text_cli.py` 做回归测试：

```bash
python text_cli.py --no-memory --no-store --no-cognition
```

当你希望模型自己读取和修改提示词时：

```bash
python text_cli.py
```

只允许读取不允许写：

```bash
python text_cli.py --read-only-tools
```

## Prompt 缓存友好性

`ChatSession.build_messages()` 会把稳定内容放在前面：

1. system prompt
2. history
3. 变化较大的摘要、记忆、屏幕、认知、情绪
4. 当前用户输入

这能让支持 prompt cache 的服务更容易复用前缀。

## Impulse 提示词

`impulse.planner_system` 决定 planner 的角色和输出格式。

`impulse.planner_user` 当前会收到：

- 当前时间
- 对话摘要
- 最近四轮对话
- 相关记忆
- 屏幕内容
- Edge 网页缓存
- 当前计划表
- cognition runtime cache
- emotion state
- 直播弹幕上下文

`impulse.trigger_system` 和 `impulse.trigger_user` 用于把计划项转成一次具体发言。

## Dialogue Orchestrator 提示词

`dialogue_orchestrator.planner_system` 和 `dialogue_orchestrator.planner_user` 用于判断角色是否该开口、沉默、短回应或稍后再说。

`dialogue_orchestrator.generator_context` 会把 planner 的决策传给发言生成器。它强调第三人称视角：场上是两个角色在对话，不是“用户”和“助手”。

`dialogue_orchestrator.reply_character_prompt` 是窄版发言生成契约，目的是减少角色背景、屏幕、弹幕、物理动作等无关内容泄漏。

`dialogue_orchestrator.screen_cache_*` 和 `dialogue_orchestrator.page_cache_*` 只描述缓存材料。是否讨论屏幕或网页由 planner 通过 `context_use` 决定。

## 修改注意

- 保持 JSON 合法。
- 模板变量名必须和代码调用一致。
- 大改 prompt 后先用 `text_cli.py --no-memory --no-store --no-cognition` 做无状态测试。
- 小模型对复杂工具调用和复杂 JSON 输出不稳定，prompt 应尽量短而明确。
