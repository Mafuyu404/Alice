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
| `bilibili_live` | 直播模式提示 |
| `tool_calling` | 工具调用结果和错误提示 |

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

## 修改注意

- 保持 JSON 合法。
- 模板变量名必须和代码调用一致。
- 大改 prompt 后先用 `text_cli.py --no-memory --no-store --no-cognition` 做无状态测试。
- 小模型对复杂工具调用和复杂 JSON 输出不稳定，prompt 应尽量短而明确。
