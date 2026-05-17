# 配置说明

## 配置层级

- `config.toml`：主配置，可提交
- `config.json`：本地密钥覆盖，不提交

## 关键字段

### 用户身份

```toml
user_name = "真冬"
```

### LLM

```toml
llm_url = "http://127.0.0.1:11434"
llm_model = "deepseek-v4-flash"
```

### TTS

```toml
tts_backend = "minimax"
tts_volume = 1.0
```

### STT

```toml
stt_model_dir = "models/stt"
stt_refine_model = "qwen2.5:1.5b"
stt_pause_during_tts = false
```

### 场景开关

```toml
[scene]
multi_enabled = false
live_enabled = false
random_mc_enabled = true
```

### 长期记忆

```toml
memory_backend = "mem0"
```

可选：

- `none`
- `mem0`
- `kokoromemo`

### 当前推荐 mem0 配置

```toml
[mem0.llm]
provider = "ollama"
base_url = "http://127.0.0.1:11434"
model = "qwen2.5:1.5b"

[mem0.embedder]
provider = "ollama"
base_url = "http://127.0.0.1:11434"
model = "bge-m3:latest"
embedding_dims = 1024
```

当前实现行为：

- 记忆根目录固定为 `mem0_data/`
- 不同 embedding 模型使用不同子目录
- 每个子目录有独立 `history.db`
- BM25 稀疏检索关闭，只使用 dense semantic search

### 记忆事件

```toml
[memory_events]
enabled = true
eval_interval = 3
eval_model = ""
```

### 屏幕与网页缓存

```toml
[screen_watch]
enabled = false

[edge_page_cache]
enabled = false
```

### 多角色调度

```toml
[multi_dialogue]
planning_model = ""
max_auto_followups = 1
log_decisions = true
```
