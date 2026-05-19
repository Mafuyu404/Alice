# 对话调度器

实现文件：`kokoro/dialogue_orchestrator.py`

## 作用

统一接管了两个原先分离的职责：

1. **话轮判断** — 用户说完一句后，决定角色应该沉默、短回应、正常说话还是稍后再说
2. **主动搭话** — 空闲时定期检查屏幕/网页缓存，决定是否值得主动开口

两个职能共用同一个 planner，角色性格同时影响话轮节奏和主动搭话倾向。

## 话轮判断（Turn Decision）

每句用户输入 → `decide(event)` → 返回动作：

| 动作 | 含义 |
|---|---|
| `silence` | 不回应，把这句话记录为"听见了但没接" |
| `backchannel` | 一句很短的轻回应，不展开话题 |
| `speak` | 正常生成回复 |
| `schedule` | 现在不说，延迟一段时间后再说 |
| `observe` | 记录但不回应 |
| `cancel_plan` | 取消所有待执行的延迟计划 |

### 判断依据

- 事件类型（用户发言、空闲检查、缓存更新）
- 角色资料（性格影响是否爱接话）
- 最近对话上下文
- 认知 / 情绪上下文
- 已有待执行计划

### 回退机制

planner 调用失败（网络、JSON 解析等）时使用 `backchannel` 作为安全回退，避免角色在系统故障时沉默。

## 主动搭话（Proactive Speech）

取代了旧的 `kokoro/impulse.py`。由后台线程驱动：

```
_dialogue_context_worker()
  └─ 每 idle_context_interval_seconds（默认 30s）
      ├─ 读取屏幕缓存 + 网页缓存
      ├─ 调用 decide(event=context_cache)
      └─ 如果决策为 speak/schedule → 加入计划表
```

### 约束

- 屏幕兴趣度低于 `context_idle_min_score` 时不作为候选
- 隐私内容跳过
- 系统忙时不触发
- 用户说话时立即取消所有待执行计划

### 计划执行（Plan Executor）

`start_plan_executor()` 启动后台线程轮询计划表。到期计划通过 `_execute_dialogue_plan()` 执行，执行时：

1. 构建回复消息（同正常对话的 build_reply_messages）
2. 流式生成回复 + TTS
3. 回复追加到历史
4. 触发肖像和情绪更新

## 与旧 impulse 的差异

| | 旧 impulse | 当前调度器 |
|---|---|---|
| planner 模型 | 独立配置 | 统一 dialogue.planning_model |
| 系统提示 | 单独一套 planner prompt | 共用 planner_system |
| 屏幕读取 | 自己调用 screen_interest | 通过 cache overview 读取 |
| 字符上下文 | 截取 system_prompt[:800] | 完整角色资料 + system_prompt[:900] |
| 空闲检测 | 独立线程 + 计划表 | 统一调度器 |

## 配置

参见 `config.toml` 的 `[dialogue]` 段：

- `planning_model` — planner 模型，留空则回退到 `impulse_model` 或 `llm_model`
- `idle_context_interval_seconds` — 空闲检查间隔
- `context_idle_min_score` — 屏幕兴趣度下限
- `max_recent_messages` — planner 可见的最近消息数
