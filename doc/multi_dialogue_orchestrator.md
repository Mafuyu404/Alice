# 多角色调度器

实现文件：`kokoro/multi_chat.py`

## 作用

管理用户 + 多个 AI 角色的三方对话。决定谁在什么时候应该说话。

```text
用户说了一句话
  │
  ▼
MultiChatOrchestrator.decide(event=user_utterance)
  │
  ├─ silence → 无人回应
  ├─ speak → 指定 speaker_id 的角色生成回复
  ├─ backchannel → 短回应
  ├─ schedule → 延迟执行
  ├─ observe → 记录但不回应
  └─ cancel_plan → 取消所有待执行计划
  │
  ▼ (speak)
build_reply_messages(decision)
  │
  ├─ 角色精简提示词 (reply_character_prompt)
  ├─ 调度边界指令 (generator_context)
  ├─ 角色区分指令
  ├─ 场景引导
  ├─ 事实边界指令
  ├─ 屏幕/网页缓存（按 decision.context_use）
  ├─ 长期记忆（按角色+对方检索）
  ├─ 认知/情绪上下文
  └─ 最近共享对话历史
  │
  ▼
agent_loop.agent_chat() → 流式回复
```

## 核心概念

### 共享历史 vs 角色独立历史

- **共享历史** (`shared_history`)：所有参与者的发言日志，以 `HistoryEntry(speaker, text, character_id)` 形式存储。这是 planner 的输入。
- **角色独立历史**：每个 `ChatSession` 维护自己的 `history`，用于自身生成回复时的上下文。

### 自动续接（auto_followup）

用户说一句话后，`user_turn()` 可以在主要回应之外自动追加 N 轮角色-角色对话：

```text
用户: "你们俩对咖啡怎么看？"
  └─ 调度器 → 爱丽丝回应
  └─ （如果 max_auto_followups ≥ 1）→ 蓬莱回应 或 爱丽丝再补一句
```

这模拟了真实对话中用户抛出话题后，角色之间可以自然交换几句再等用户介入。

### 空闲轮询（auto_turn）

后台定时调用 `auto_turn()`：
1. 检查是否有到期计划要执行
2. 检查页面场景是否切换
3. 如果都无，调用 `decide(event=idle_tick)`
4. 返回 (character_id, name, reply)

## 场景

### 多人聊天

普通的多角色文本对话，role 标记区分说话人。

### 多人直播

多角色 + 直播场景，弹幕作为额外输入。
- `live_enabled = true` → `scene_guidance` 注入直播理解方式
- 弹幕内容通过 `extra_context` 送入 planner

### 页面讲解/研究场景

- `page_scene_enabled = true`
- 页面切换时 `page_scene_changed` 事件强制角色转向新页面
- Idle tick 时维持讲解活跃度，不轻易 silence
- 每个角色从自己的角度评价同一页面

## Planner 回退

planner 失败（网络/JSON 解析问题）时：

```text
user_utterance fallback → 由发言最少的角色接话
character_utterance fallback → 由另一个角色接上一句
idle_tick + 页面场景 + 页面变化 → 强制转向当前页
```

## 预取（Prefetch）

看板模式下（`--watch`），当前一个角色刚说完，后台立即预取下一个角色的 possible 回应：

```text
prepare_followup_turn(speaker_id, speaker, text)
  → 异步执行 planner + 生成
  → 缓存结果

commit_prepared_turn(prepared)
  → 如果期间状态没变，直接提交缓存结果（零延迟）
```

预取状态用 serial number 跟踪——如果新用户输入插入，过期的预取结果被丢弃。

## 事实锚定

多角色场景中角色容易相互"肯定"对方编造的内容。当前约束：

- 每个角色的生成 prompt 注入事实边界指令（"不要编造页面内容、代码结构、变量名"）
- 上一轮有人说错时，下一轮不能继续承认或扩写
- 证据不足时说"不确定"，而不是编具体场景
