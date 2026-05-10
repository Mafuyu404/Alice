# 会话与人格层

`kokoro/chat_session.py` 负责一次角色会话的上下文组装、历史管理、记忆写入、摘要更新、认知缓存刷新和情绪评估。

## Message 组装顺序

`ChatSession.build_messages()` 生成的 messages 顺序是：

1. 角色 system prompt
2. 历史对话
3. 对话摘要
4. 屏幕上下文
5. 额外上下文
6. 长期记忆检索结果
7. cognition runtime cache
8. emotion state
9. STT inline 精炼提示
10. 当前用户输入

`text_cli.py` 默认 `include_screen=False`，不会注入屏幕上下文。

## 历史和摘要

会话历史保存在内存中。超过 `max_window` 后，会把最早的 `compress_batch` 条消息异步压缩进 `summary`，并保存到角色摘要文件。

摘要用于：

- 后续对话上下文
- 记忆检索 query
- cognition 评估
- impulse 规划

## 记忆

`remember()` 在回复结束后执行：

- 把 user / assistant 追加进 history
- 异步写入长期记忆
- 刷新 cognition runtime cache
- 异步评估 emotion
- 周期性评估 cognition

`text_cli.py --no-store` 会跳过 `remember()`，适合无副作用测试。

## Cognition

`kokoro/cognition.py` 维护两层数据：

- 完整认知：`characters/{id}/cognition.json`
- runtime cache：当前对话相关的认知子集

普通聊天和 impulse 规划都只注入 runtime cache，不注入完整 `cognition.json`。

runtime cache 的刷新是轻量规则：

- 当前 user / assistant 文本中命中 cognition key
- 始终保留一部分优先级 key
- 始终保留关系相关 key

完整 cognition 评估由 LLM 异步执行，频率由 `cognition_eval_interval` 控制。

## Emotion

`kokoro/emotion.py` 维护：

- `tone`：当前情绪基调
- `motivation`：近期动机

保存位置：

```text
characters/{id}/emotion.json
```

普通聊天和 impulse 规划都会注入 emotion context。为空时不注入。

## 对人格测试的建议

稳定回归测试时使用：

```bash
python text_cli.py --no-memory --no-store --no-cognition
```

需要测试长期人格变化时再打开：

```bash
python text_cli.py
```

这样能区分“当前 prompt 表现”和“长期状态污染”。

## Cognition 专项测试

普通聊天测试不能替代 cognition evaluator 测试。角色在聊天中会把 `key`、`value`、`cognition` 当成普通话题，而不是严格执行认知维护规则。

认知层专项测试：

```bash
python tests/run_cognition_eval_cases.py
```

该脚本会备份真实 cognition 文件，调用 `CognitionStore.evaluate()`，检查长期 key 和短期污染 key，然后恢复原文件。
