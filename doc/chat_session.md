# 会话与人格层

实现文件：`kokoro/chat_session.py`

## 职责

`ChatSession` 是单角色对话的核心数据类，负责：

1. 为 LLM 调用组装完整的 messages 数组
2. 维护对话历史和滚动摘要
3. 注入长期记忆、认知层、情绪层
4. 对话后触发各种异步维护（记忆存储、认知评估、情绪评估、事件提取）

## 数据结构

```python
@dataclass
class ChatSession:
    character_id: str                 # 角色 ID
    character_data: dict              # 角色 JSON 原始数据
    memory_backend: object            # 记忆后端
    user_name: str = "你"            # 当前用户称呼
    max_history: int = 20            # 历史保留轮数
    history: list[dict]              # 消息历史 [{"role","content"}, ...]
    max_window: int = 40             # 超过此数量触发摘要压缩
    compress_batch: int = 10         # 每次压缩最旧的 N 条
    summary: str = ""                # 对话运行摘要
    cognition: CognitionStore        # 认知层实例
    emotion: EmotionState            # 情绪层实例
    cognition_eval_interval: int = 5 # 认知评估频率（轮）
    memory_events: MemoryEventStore  # 结构化事件存储器
    _scene: SceneType                # 当前场景类型
    screen_contexts: list[str]       # 屏幕上下文缓存
```

## build_messages() 流程

这是最关键的方法——把角色状态组装成 LLM 可消费的 messages 列表。

```text
messages = [
  {"role": "system", "content": system_prompt},   # 角色主提示词
  ... history[] ...                                 # 对话历史（缓存友好前缀）
  {"role": "system", "content": "【对话摘要】..."}, # 运行摘要
  {"role": "system", "content": "【当前场景】..."}, # 场景引导
  {"role": "system", "content": "屏幕上下文..."},   # 最近屏幕分析结果
  {"role": "system", "content": extra_context...},  # 外部上下文（命令结果等）
  {"role": "system", "content": "【记忆】..."},     # 长期记忆
  {"role": "system", "content": "【认知】..."},     # 认知层
  {"role": "system", "content": "【当前情绪】..."}, # 情绪层
  (可能: {"role": "system", "content": "STT inline 纠错提示"})
  {"role": "user", "content": user_text},          # 当前用户输入
]
```

### 注入顺序的设计意图

1. System prompt + history 放在最前面 → DeepSeek / OpenAI 的前缀缓存能覆盖这些稳定内容
2. 动态内容（摘要、场景、记忆、认知、情绪）放在 history 之后 → 不影响缓存前缀
3. 用户输入放最后 → 对应请求体尾部

## 历史摘要机制

当 `len(history) > max_window`（默认 40 条）时触发：

1. 锁定 `_summarize_lock`
2. 取出最旧的 `compress_batch` 条消息
3. 从 history 中移除这批消息
4. 异步调用 `_summarize_async()` 将这批消息合并到现有 summary
5. 摘要模型使用 `stt_refine_model`（小模型，不需要和对话同一级别）→ `_call_summary_llm()`

### 摘要提示词

- system：「你是一个对话摘要助手。请将新对话内容合并到已有摘要中，输出更新后的摘要。」
- user：已有摘要 + 新对话内容 → 输出 3-5 句中文摘要

## 对话后异步链

每条对话完成后，`remember()` 触发以下异步任务：

```text
remember(user_text, assistant_text)
  │
  ├─ 追加到 history
  │
  ├─ [条件] history 超限 → _summarize_async()
  │   └─ LLM 摘要 → 更新 self.summary
  │   └─ 摘要后 → cognition.evaluate() 全量评估
  │
  ├─ memory_events.on_conversation_turn() → 事件提取
  │   └─ LLM 提取 → 缓存/写入 mem0
  │
  ├─ cognition.refresh_cache() → 关键词匹配，无 LLM
  │
  ├─ emotion.evaluate() → LLM 情绪评估（异步）
  │
  └─ [每 N 轮] → _eval_cognition_async() → LLM 认知评估
```

## 与调度器的关系

`ChatSession` 本身不问"该不该说"。它只提供：

- `build_messages()` — 组装提示词
- `remember()` — 对话后维护

"什么时候说"由 `DialogueOrchestrator` 决定。调度器可以通过 `build_reply_messages()`（更窄的提示词，节 token）或 `ChatSession.build_messages()`（完整提示词）两种途径生成回复。
