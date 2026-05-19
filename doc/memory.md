# 记忆系统

记忆系统由两层组成：

1. 向量记忆后端
2. 事件式记忆抽取与总结

## 后端类型

- `none`
- `mem0`
- `kokoromemo`

## 当前推荐方案

```toml
memory_backend = "mem0"
```

并配合：

- `mem0.llm = ollama`
- `mem0.embedder = bge-m3:latest`

## 当前目录结构

- 根目录：`mem0_data/`
- 子目录：按 embedding 模型区分
- 每个子目录包含：
  - 本地 qdrant 数据
  - `history.db`

## 设计原则

- 记忆层存事件
- cognition 层存稳定印象
- inner stream 层吸收当前情绪、短期动机和表达连续性

## memory_events

`memory_events` 会在每轮对话后做两步：

1. 从本轮对话里提取具体事件
2. 每隔若干轮做去重、合并、稳定性判断

输出结果分成两类：

- `stable`：写入长期记忆
- `cache`：暂存在内存中，等待后续合并

## 当前 mem0 行为

- telemetry 已关闭
- BM25 稀疏检索已关闭
- 默认只使用 dense semantic search

## 查看记忆

```bash
python memory_viewer.py
```

说明：

- Windows 非 UTF-8 控制台可能把中文显示成 `?`
- 这不等于记忆损坏
- 查看中文内容时优先使用 `memory_viewer.py` 或 `/api/memories`
