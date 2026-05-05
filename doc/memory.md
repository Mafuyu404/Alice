# 记忆系统

## 架构

记忆系统包含两个独立模块：

1. **记忆后端** (`kokoro/memory.py`) — 长期记忆的存储、检索和写入
2. **记忆事件** (`kokoro/memory_events.py`) — 定期轮询触发记忆相关主动行为

## 记忆后端

`MemoryBackend` 抽象基类，三种实现：

| 实现 | `memory_backend` 配置 | 说明 |
|------|----------------------|------|
| `Mem0Backend` | `"mem0"` | 本地向量数据库，mem0ai + fastembed + Qdrant |
| `KokoroMemoBackend` | `"kokoromemo"` | 外部 HTTP 记忆服务（stub 实现） |
| `NoMemoryBackend` | `"none"` | 无操作，禁用记忆 |

通过 `create_backend(config)` 工厂函数创建。

### 基类接口

```python
class MemoryBackend:
    def get_context(self, query: str, user_id: str = "default") -> str: ...
    def store(self, user_msg: str, assistant_msg: str, user_id: str = "default") -> None: ...
    @property
    def ready(self) -> bool: ...
```

### Mem0Backend

```
用户输入 → LLM 判断重要度 → 存入 mem0 向量库
                                    ↓
                       新对话时语义搜索 → 返回相关记忆
```

- **重要度过滤**：`importance_mode = "auto"` 时，调用 LLM 判断每条对话是否值得记忆（返回含"不重要"则跳过）；`"always"` 时仅做简单过滤（超短文本和单字回复跳过）
- **语义搜索**：fastembed 将查询转为向量，Qdrant 余弦相似度匹配，`search_threshold`（默认 0.3）控制召回精度，`search_top_k`（默认 8）限制最大返回条数
- **时间标签**：搜索结果附带时间标签（今天/昨天/N天前/月-日）
- **自动压缩**：每 `compress_interval`（默认 50）条记忆后触发清理，超过 `max_memories_per_user`（默认 200）时删除最早的记忆
- **复用 LLM 连接**：使用 `[mem0.llm]` 配置中的 Ollama 地址和模型

### 记忆注入

`ChatSession.build_messages()` 调用 `memory_backend.get_context(user_text, user_id=character_id)` 获取与当前输入相关的记忆，以 system 消息形式插入到对话上下文中（格式为 `【记忆】\n- [时间标签] 记忆内容`）。

## 记忆事件

`kokoro/memory_events.py` 中的 `MemoryEventDetector` 周期性触发两种事件：

### 日期匹配
在 `config.toml` 的 `[[proactive.memory_date_events]]` 中预定义日期事件：

```toml
[[proactive.memory_date_events]]
date = "05-04"
label = "Alice project anniversary"
note = "Mention it only if the user seems idle and the mood is relaxed."
```

支持 `YYYY-MM-DD`（精确年份）和 `MM-DD`（每年匹配）两种格式。当前日期匹配时，向 proactive 调度器注入 MEM 冲动值。

### 定期记忆查询
按 `memory_check_interval`（默认 300 秒）轮询，使用预设的 `memory_lookup_query` 从记忆后端搜索，结果注入 MEM 冲动值。搜索结果经 `_compact_context()` 压缩（去标题行、截断至 700 字符、最多 4 行）。

### 冷却机制
同一记忆事件触发后进入 `memory_cooldown_seconds`（默认 6 小时）冷却期，避免重复。日期事件的冷却 key 为 `date:{date}:{index}`，记忆查询事件的冷却 key 为内容的稳定哈希。

### 配置

记忆事件配置位于 `config.toml` 的 `[proactive]` 节下：

```toml
[proactive]
memory_events_enabled = true
memory_check_interval = 300.0       # 轮询间隔（秒），最小 30
memory_cooldown_seconds = 21600.0   # 冷却时间（6 小时），最小 60
memory_date_score = 50.0            # 日期匹配的基础冲动值
memory_lookup_score = 70.0          # 记忆查询结果的基础冲动值
memory_lookup_query = "recent important user preferences, plans, dates, anniversaries, goals"
```

### 主循环集成

`cli.py` 在独立线程中运行 `memory_event_worker()`，周期调用 `memory_detector.poll()` → `scheduler.add_memory_interest()` → `memory_detector.mark_emitted()`。
