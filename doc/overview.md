# 架构概览

## 系统分层

```text
┌──────────────────────────────────────────────────────────┐
│                    入口层 (Entry)                         │
│  cli.py (STT/TTS/立绘/全功能)   text_cli.py (纯文本调试)  │
│  run_multi.py (多人看板)         memory_viewer.py          │
└─────────────────────┬────────────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────────────┐
│                  调度层 (Orchestration)                    │
│  dialogue_orchestrator  ─ 话轮判断 + 主动搭话 + 计划执行  │
│  multi_chat            ─ 多角色: 谁说、对谁说、说什么       │
└─────────────────────┬────────────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────────────┐
│                  会话层 (Session)                          │
│  chat_session  ─ 角色提示词组装、历史、摘要、上下文注入     │
│  依赖: memory / cognition / emotion / scene / prompts     │
└─────────────────────┬────────────────────────────────────┘
          ┌───────────┼───────────┐
          ▼           ▼           ▼
┌─────────────────┐ ┌─────────┐ ┌──────────────────────┐
│  感知层 (Input)  │ │ 输出层   │ │  记忆与人格层          │
│  stt / 语音识别   │ │ tts     │ │  mem0 向量记忆         │
│  conversation    │ │ 立绘    │ │  memory_events 事件提取 │
│  屏幕兴趣度       │ │ 字幕    │ │  cognition 稳定认知     │
│  Edge 页面缓存    │ │ VTS    │ │  emotion 情绪基调      │
│  Bilibili 弹幕   │ │        │ │                      │
└─────────────────┘ └─────────┘ └──────────────────────┘
```

## 数据流：一次完整对话

```text
用户说话
  │
  ▼
STT 流式识别 (sherpa-onnx)
  │
  ▼
ConversationManager.feed_audio()
  ├─ AEC 回声消除
  ├─ 端点检测 (silence-based)
  ├─ 重叠分类 (overlap classifier)
  │   └─ continue / soft_break / hard_break
  │
  ▼
on_user_utterance(text)
  │
  ├─ DialogueOrchestrator.decide(event=user_utterance)
  │   ├─ silence  → 记录"听见了"，不回应
  │   ├─ schedule → 加延迟计划，稍后执行
  │   ├─ speak    → 进入回复生成
  │   └─ backchannel → 短回应后收工
  │
  ▼ (speak 路径)
ChatSession.build_messages()
  ├─ 角色 system prompt
  ├─ 对话历史
  ├─ 对话摘要
  ├─ 场景引导 (scene guidance)
  ├─ 屏幕/网页上下文 (由调度器决定)
  ├─ 长期记忆 (mem0 检索)
  ├─ 认知层上下文 (cognition cache)
  ├─ 情绪层上下文 (emotion)
  └─ 用户输入
  │
  ▼
agent_loop / chat_stream → LLM 流式回复
  ├─ 逐 token 输出 → 打印 / TTS / 字幕
  ├─ 工具调用 (look_at_screen, search_memory 等)
  └─ 完整回复
  │
  ▼
session.remember()
  ├─ 追加到历史
  ├─ 触发摘要压缩 (历史超限时)
  ├─ 触发情绪评估 (每轮异步)
  ├─ 触发记忆事件提取 (每 N 轮)
  ├─ 触发认知评估 (每 N 轮)
  └─ 触发记忆存储 (mem0)
```

## 空闲时主动搭话流

```text
_dialogue_context_worker (每 30s)
  │
  ├─ 读屏幕缓存 (screen_interest cache)
  ├─ 读网页缓存 (edge cache)
  │
  ▼
DialogueOrchestrator.decide(event=context_cache)
  ├─ silence → 继续等下次 tick
  ├─ speak   → 立即加入计划表
  └─ schedule → 延迟加入计划表
  │
  ▼
PlanExecutor 到期执行
  ├─ build_reply_messages()
  ├─ chat_stream() → 生成 + TTS
  └─ 追加到历史
```

## 各层关键设计决策

| 层 | 决策 | 理由 |
|---|---|---|
| 入口 | cli.py 单线程事件驱动 + 工作线程 | STT 实时性要求高，LLM 可后台 |
| 调度 | planner 先判断再生成 | 避免每句都生成回复但被沉默浪费 token |
| 调度 | 主动搭话与话轮判断用同一个 planner | 角色性格一致影响两种场景 |
| 会话 | 所有上下文作为 system message 注入 | 保持 LLM 看到完整信息，历史压缩只做数量控制 |
| 会话 | history 缓存 + 异步摘要 | 不阻塞主对话流 |
| 感知 | AEC 软方案 | 不需要额外硬件，纯算法消除回音 |
| 记忆 | mem0 + Ollama embedding | 全本地运行，数据不出机器 |
| 记忆 | 事件式提取 + 周期性总结 | 原始对话存 mem0 质量差，结构化后更可控 |
