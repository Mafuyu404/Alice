# 记忆系统

## 架构

记忆系统包含两个独立模块：

1. **记忆后端** (`kokoro/memory.py`) — 长期记忆的存储、检索和写入
2. **记忆事件** (`kokoro/memory_events.py`) — 定期轮询触发记忆相关主动行为

## 记忆后端

`MemoryBackend` 抽象基类，三种实现：

| 实现 | 后端类型 | 说明 |
|------|---------|------|
| `Mem0Backend` | `mem0` | 本地向量数据库，使用 mem0ai + Qdrant |
| `KokoroMemoBackend` | `kokoromemo` | 外部 HTTP 记忆服务 |
| `NoMemoryBackend` | `none` | 无操作，禁用记忆 |

通过 `create_backend()` 工厂函数根据配置创建。

### Mem0Backend

```
用户输入 → LLM 判断重要度 → 存入 mem0 向量库
                                    ↓
                       新对话时语义搜索 → 返回相关记忆
```

- **重要度过滤** (`importance_mode = "auto"`)：LLM 判断每条对话是否值得记忆
- **语义搜索**：使用 fastembed 将查询转为向量，余弦相似度匹配
- **自动压缩**：当单条记忆超过 token 限制时递归摘要压缩
- **复用配置中的 LLM 连接**（Ollama 地址、模型名）

### 记忆注入

`ChatSession.build_messages()` 在构建请求时调用 `memory_backend.search()`，将相关记忆以单独消息的形式注入到 system prompt 之后或历史之前（由 `memory_injection_mode` 控制）。

## 记忆事件

`kokoro/memory_events.py` 中的 `MemoryEventDetector` 周期性触发两种事件：

### 日期匹配
`config.toml` 中预定义的 `[[proactive.memory_date_events]]`：

```toml
[[proactive.memory_date_events]]
date = "05-04"
label = "Alice project anniversary"
note = "Mention it only if the user seems idle and the mood is relaxed."
```

当当前日期匹配时，向 proactive 调度器注入记忆兴趣。

### 定期记忆查询
按 `memory_check_interval`（默认 300 秒）轮询，用预设的 `memory_lookup_query` 从记忆后端搜索并注入兴趣。

### 冷却机制
同一记忆事件触发后有 `memory_cooldown_seconds`（默认 6 小时）的冷却期，避免重复触发。

### 配置

```toml
memory_events_enabled = true
memory_check_interval = 300.0
memory_cooldown_seconds = 21600.0
memory_date_score = 50.0
memory_lookup_score = 70.0
memory_lookup_query = "recent important user preferences, plans, dates, anniversaries, goals"
```
