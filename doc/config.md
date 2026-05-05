# 配置系统

## 双层配置

框架采用双层配置机制：`config.toml` 作为主配置文件，`config.json` 作为本地密钥覆盖文件（已加入 `.gitignore`）。

### 合并规则

`kokoro/config.py` 中的 `_merge_fallback(primary=toml, fallback=json)`：

- TOML 中已存在的非空值优先
- JSON 填充 TOML 中缺失或为空的值
- 嵌套字典递归合并

### 适用场景

| 文件 | 内容 | 是否提交 git |
|------|------|-------------|
| `config.toml` | 通用配置：模型地址、后端选择、各模块参数 | 是 |
| `config.json` | 敏感密钥：API Key、Voice ID | 否（已 gitignore） |

## API 密钥

```json
{
  "deepseek_api_key": "sk-xxx",
  "minimax_api_key": "sk-xxx",
  "cartesia_api_key": "sk-xxx",
  "vision_api_key": "sk-xxx",
  "tts_voice_id": "xxx"
}
```

部分密钥也可通过环境变量设置（`config.py` 优先读取环境变量）：
- `DEEPSEEK_API_KEY`
- `DASHSCOPE_API_KEY`

## 配置加载流程

1. `kokoro/config.py.load()` 首次调用时加载，后续返回缓存
2. 清理所有 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量，设置 `NO_PROXY=*`
3. 读取 `config.toml`（使用 `tomllib`，Python 3.11+ 内置）
4. 读取 `config.json`（密钥覆盖）
5. 合并两层配置，全局缓存
6. 各辅助函数（`llm_url()`、`tts_backend()` 等）内部调用 `get()` → `load()`

## 配置项总览

所有配置项及详细注释直接写在 `config.toml` 中。以下是各模块包含的主要配置组：

### LLM

`llm_url` `llm_model` `available_models` `deepseek_api_key` `local_model`

LLM 地址兼容 OpenAI/Ollama 格式。模型名以 `deepseek` 开头时自动路由到 DeepSeek 云端 API。

### TTS

`tts_backend` — `"minimax"` 或 `"cartesia"`，切换后自动加载对应后端模块。

MiniMax 特有：`minimax_model` `minimax_tts_speed` `minimax_tts_buffer_seconds`

流式 TTS：`tts_stream_chunk_chars`（累积多少字符强制刷新缓冲区） `tts_stream_sentence_min_chars`（触发刷新的最小字符数，需配合句末标点）

### STT

`stt_model_dir` `stt_refine_model` `stt_refine_stable_seconds`（静默判定时间，默认 0.6） `stt_pool_tick_seconds` `stt_refine_max_tokens` `stt_skip_short_refine` `stt_skip_short_refine_max_chars` `stt_pause_during_tts`

### 记忆

`memory_backend` — `"none"`（禁用）`"mem0"`（本地向量库）`"kokoromemo"`（外部服务）

mem0 子配置：`[mem0.llm]` `[mem0.embedder]` `[mem0.lifecycle]`

### 立绘

`portrait_overlay_host/port` `portrait_decision_interval` `portrait_decay_seconds` `portrait_debug_overlay` `portrait_click_through`

### 视觉 / 屏幕识别

`vision_backend` `vision_model` `vision_api_key`

### 主动搭话调度器

全局 `[proactive]` + 四类行为子配置：`[proactive.idle]` `[proactive.recent]` `[proactive.mem]` `[proactive.screen]`

详见 [proactive.md](proactive.md)。

### 屏幕监控

`[screen_watch]`：`watch_interval` `interest_threshold` `vision_timeout` 以及记忆事件子配置。

详见 [screen_interest.md](screen_interest.md)。
