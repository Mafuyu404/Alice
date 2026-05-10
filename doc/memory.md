# 记忆

记忆后端由 `kokoro/memory.py` 提供，支持三种模式：

- `none`
- `mem0`
- `kokoromemo`

## 配置

```toml
memory_backend = "mem0"
```

KokoroMemo：

```toml
kokoromo_url = "http://127.0.0.1:14514"
kokoromo_dir = "D:/program/kokoromemo"
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

## 记忆事件

`memory_events.py` 会按配置检查某些时间或查询事件，并把结果作为上下文注入会话。它由完整 CLI 启动，`text_cli.py` 不启动。

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
