# 记忆系统

记忆系统由两层组成：**向量记忆后端**（存储和检索）和 **事件式记忆抽取**（结构化提取）。

## 架构总览

```text
每轮对话后:
  user_text + assistant_text
    │
    ├─→ memory_events.on_conversation_turn()
    │     │
    │     ├─ LLM 事件提取 (结构化)
    │     │     "用户展示了他的咖啡拉花作品"
    │     │     tags: [偏好, 饮食, 咖啡, 拉花]
    │     │
    │     ├─ pending_events[] 累积
    │     │
    │     └─ [每 N 轮] LLM 去重合并 + 稳定性判断
    │           ├─ stable → 写入 mem0 长期记忆
    │           └─ cache → 保留在内存
    │
    └─→ memory_backend.store() (mem0)
          │
          ├─ 重要度判断 (LLM / 规则)
          └─ 写入向量库 + history.db
```

## 向量记忆后端

### 后端类型

| backend | 实现类 | 说明 |
|---|---|---|
| `mem0` | `Mem0Backend` | 本地向量库，推荐方案 |
| `kokoromemo` | `KokoroMemoBackend` | 外部记忆服务 |
| `none` | `NoMemoryBackend` | 无记忆 |

### mem0 方案

```toml
memory_backend = "mem0"

[mem0.embedder]
provider = "ollama"
model = "bge-m3:latest"
embedding_dims = 1024
```

检索流程：

```text
get_context(query, user_id)
  │
  ├─ mem0.search(query, filters={user_id}, top_k=12)
  ├─ 结果按 score 降序排列
  ├─ 去重（归一化文本重叠检查）
  ├─ 格式化为 "【记忆】- [标签] 内容"
  └─ 返回字符串（空结果返回空字符串）
```

数据位置：`mem0_data/{model_slug}_{dims}d/`
- 本地 Qdrant 向量数据
- `history.db` 操作记录

### 重要度控制

`importance_mode`：
- `auto`：LLM 判断是否重要（默认，准确但调用量大）
- `always`：全部存储（记忆膨胀快）

LLM 判断使用 `mem0.llm` 中配置的小模型（默认 `qwen2.5:1.5b`），提示词判断"值得记忆" vs "日常寒暄"。

### 生命周期

- 每 `compress_interval`（50 条）次存储后，清理超出 `max_memories_per_user` 的最旧条目
- 检索阈值 `search_threshold` 过滤低相似度结果（默认 0.2）

## 事件式记忆（memory_events）

### 动机

原始对话对存 mem0 质量不高。事件式记忆通过 LLM 将对话提炼为结构化事件，再写入长期记忆。

### 事件提取

每轮对话调用 `memory_events.extract_system` + 对话内容 → LLM 返回 JSON 数组：

```json
[
  {
    "desc": "真冬展示了他最近练习的咖啡拉花照片，是一颗还算对称的叶子图案。爱丽丝评价说比上次进步明显，奶泡厚度控制得不错。",
    "tags": ["偏好", "饮食", "咖啡", "拉花", "进步"]
  }
]
```

### 去重合并（周期性）

每 `eval_interval` 轮：
1. 收集 `pending_events` + `summary_cache`
2. LLM 去重合并 → `stable`（写入 mem0）+ `cache`（保留）
3. stable 事件写入后从 pending 移除

### 命名实体锚点

提取提示词要求保留具体名称锚点：

- 人名/昵称/主播名/角色名 → 保留原名
- 具体领域对象/百科页面 → 保留至少一个具体对象名
- 禁止把明确对象压缩成"某个项目""一个作品"

## 记忆检索与注入

`ChatSession.build_messages()` 中通过 `memory_backend.get_context_multi()` 检索：

```text
query = user_text（当前用户输入）
user_ids = [
  "{character_id}::counterpart::{user_name}",  # 对方视角
  "{character_id}::general",                    # 角色全局
  "{character_id}"                              # 兜底
]
```

检索结果注入为 system message，放在 history 之后、用户输入之前。

## 记忆查看

```bash
python memory_viewer.py
```

支持：
- 按角色查看
- 按内容搜索
- 删除单条记忆
- 清空全部记忆

注意：Windows 非 UTF-8 控制台可能把中文显示为 `?`，这不等于记忆损坏。
