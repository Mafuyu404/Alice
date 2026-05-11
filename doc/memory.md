# 记忆

记忆系统由两层组成：

1. **向量记忆后端** — 持久化存储（mem0 / none / kokoromemo）
2. **记忆事件系统** — LLM 驱动的结构化事件提取和总结（叠加在向量后端之上）

## 向量记忆后端

由 `kokoro/memory.py` 提供，支持三种模式：

- `none`
- `mem0`
- `kokoromemo`

### 配置

```toml
memory_backend = "mem0"
```

KokoroMemo：

```toml
kokoromo_url = "http://127.0.0.1:14514"
kokoromo_dir = "D:/program/kokoromemo"
```

### ChatSession 中的记忆

`ChatSession.build_messages()` 会根据当前用户输入检索记忆，并把结果注入 messages。

`ChatSession.remember()` 在每轮回复后触发记忆事件提取和存储周期。

## 记忆事件系统

`kokoro/memory_events.py` 中的 `MemoryEventStore` 取代了原始的对话对存储。

### 工作流程

```
每轮对话 → LLM 事件提取 → pending_events（内存缓存）
                             │
                每 N 轮（interval）→ LLM 总结 → ─ stable → mem0（向量库）
                                                 └ cache → 继续缓存
                             │
                进程关闭 → flush_all → pending + cache → mem0
```

### 事件提取

每次对话后，LLM 从本轮对话中提取独立、具体的事件。每个事件包含：

- `desc`：事件描述（一句话）
- `tags`：标签数组（1-3 个，方便回想）

提取通过提示词 `memory_events.extract_system` / `extract_user` 驱动，不写死逻辑。

### 缓存周期

提取的事件先存放在内存的 `pending_events` 中。每 `eval_interval` 轮对话，LLM 对 pending 和 cache 中的事件进行：

- **去重合并**：合并高度相似的事件
- **稳定性判断**：哪些事件上下文已不再涵盖 → 写入 mem0（stable）
- **活跃保留**：哪些事件仍需跟踪 → 留在缓存（cache）

### 关闭刷入

对话进程关闭时（finally 块），所有 pending 和 cache 中的事件一次性刷入 mem0。

### 配置

```toml
[memory_events]
enabled = true
eval_interval = 3
eval_model = ""
```

### 标签显示

检索记忆时，事件标签会以 `#标签` 形式显示在结果中：

```
【记忆】
- [今天] #偏好 #咖啡 真冬说喜欢喝美式咖啡
- [昨天] #计划 #户外 真冬提到周末想去露营
```

## ChatSession 中的记忆

`ChatSession.build_messages()` 会根据当前用户输入检索记忆，并把结果注入 messages。

`ChatSession.remember()` 会在回复后异步写入本轮对话。

关闭写入：

```bash
python text_cli.py --no-store
```

关闭记忆后端：

```bash
python text_cli.py --no-memory
```

完整 CLI 临时关闭需要把配置改为：

```toml
memory_backend = "none"
```

## 工具调用

完整 CLI 支持：

- `search_memory`
- `save_to_memory`

文字 CLI 的文件工具不等同于长期记忆工具。

## 记忆事件（旧）

`MemoryEventDetector` 会按配置检查某些时间或查询事件，并把结果作为上下文注入会话。它由完整 CLI 启动，`text_cli.py` 不启动。

## 查看记忆

```bash
python memory_viewer.py
```

默认用于查看本地 mem0 记忆。

## 人格测试建议

做 prompt 回归测试时建议：

```bash
python text_cli.py --no-memory --no-store --no-cognition
```

这样输出更可重复，不会被长期记忆影响。
