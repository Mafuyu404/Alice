# 配置系统

## 双层配置

框架采用双层配置机制：`config.toml` 作为主配置文件，`config.json` 作为本地密钥覆盖文件（已加入 `.gitignore`）。

### 合并规则

`kokoro/config.py` 中的 `_merge_fallback(primary=toml, fallback=json)`：

- TOML 中已存在的非空值优先（`None`、`""`、`[]`、`{}` 视为空）
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
- `DEEPSEEK_API_KEY` — DeepSeek API 密钥（环境变量优先级高于 config.json）
- `DASHSCOPE_API_KEY` — 阿里云 DashScope 视觉 API 密钥

## 配置加载流程

1. `kokoro/config.py.load()` 首次调用时加载，后续返回全局缓存 `_CONFIG`
2. 清理所有 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量，设置 `NO_PROXY=*`
3. 读取 `config.toml`（使用 `tomllib`，Python 3.11+ 内置）
4. 读取 `config.json`（密钥覆盖）
5. 合并两层配置，存入全局变量
6. 各辅助函数（`llm_url()`、`tts_backend()` 等）内部调用 `get()` → `load()`

## 配置访问函数

`kokoro/config.py` 提供以下辅助函数，封装了默认值和环境变量读取：

| 函数 | 默认值 | 说明 |
|------|--------|------|
| `llm_url()` | `http://127.0.0.1:11434` | LLM 服务地址 |
| `llm_model()` | `deepseek-v4-flash` | 默认对话模型 |
| `memory_backend()` | `mem0` | 记忆后端类型 |
| `tts_backend()` | `minimax` | TTS 后端选择 |
| `tts_sample_rate()` | `24000` | Cartesia TTS 采样率 |
| `tts_voice_id()` | `""` | Cartesia 语音 ID |
| `cartesia_api_key()` | `""` | Cartesia API 密钥 |
| `minimax_api_key()` | `""` | MiniMax API 密钥 |
| `minimax_model()` | `speech-2.8-turbo` | MiniMax 语音模型 |
| `kokoromo_url()` | `""` | KokoroMemo 服务地址 |
| `deepseek_api_key()` | 环境变量 > config.json | DeepSeek API 密钥 |
| `charglm_api_key()` | config.json | 智谱 CharGLM API 密钥 |
| `deepseek_url()` | `https://api.deepseek.com` | DeepSeek API 地址 |
| `is_deepseek_model(m)` | — | 模型名是否以 `deepseek` 开头 |
| `vision_max_pixels()` | `921600` | 截图缩放上限像素数，`0` 禁用缩放 |
| `stt_refine_model()` | `qwen2.5:1.5b` | STT 精炼用模型 |
| `stt_refine_mode()` | `separate` | 精炼模式 |
| `stt_refine_stable_seconds()` | `1.5` | 静默判定时间（秒） |
| `stt_pool_tick_seconds()` | `0.05` | 池轮询间隔（秒） |
| `stt_refine_max_tokens()` | `128` | 精炼 LLM 最大 token |
| `stt_skip_short_refine()` | `True` | 是否跳过短文本精炼 |
| `stt_skip_short_refine_max_chars()` | `18` | 跳过精炼的最大字符数 |
| `stt_pause_during_tts()` | `False` | TTS 播放时暂停 STT |
| `api_base()` | 动态计算 | LLM API base URL（含 `/v1`） |

## 配置项总览

所有配置项及详细注释直接写在 `config.toml` 中。以下是各模块包含的主要配置组：

### LLM

`llm_url` `llm_model` `deepseek_api_key` `local_model`

- LLM 地址兼容 OpenAI/Ollama 格式，程序自动补 `/v1`
- 模型名以 `deepseek` 开头时自动路由到 DeepSeek 云端 API（`api.deepseek.com`）
- `local_model` 是 HuggingFace 模型 ID，作为 LLM 不可用时的后备（`local_llm.py` 使用）

### TTS

`tts_backend` — `"minimax"` 或 `"cartesia"`，切换后自动加载对应后端模块（`kokoro/tts_{backend}.py`）

#### Cartesia

`cartesia_api_key` `tts_voice_id` `tts_sample_rate`（默认 24000）

#### MiniMax

`minimax_api_key` `minimax_model`（默认 `speech-2.8-turbo`） `minimax_sample_rate`（默认 32000） `minimax_tts_speed` `minimax_tts_buffer_seconds`

#### 流式控制（两个后端共用）

`tts_stream_chunk_chars`（累积字符阈值，默认 28） `tts_stream_sentence_min_chars`（触发刷新的最小字符数，默认 8）

### STT

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `stt_model_dir` | `models/stt` | 模型存放目录 |
| `stt_refine_model` | `qwen2.5:1.5b` | 精炼用小模型 |
| `stt_refine_mode` | `separate` | 精炼模式：`separate` / `inline` / `none` |
| `stt_refine_stable_seconds` | `1.0` | 静默判定时间（秒） |
| `stt_pool_tick_seconds` | `0.05` | 池轮询间隔（秒） |
| `stt_refine_max_tokens` | `128` | 精炼 LLM 最大输出 token |
| `stt_skip_short_refine` | `true` | 是否跳过短文本精炼 |
| `stt_skip_short_refine_max_chars` | `18` | 跳过精炼的最大字符数 |
| `stt_pause_during_tts` | `true` | TTS 播放时暂停麦克风 |

`stt_refine_mode` 三种模式：

- **`separate`**（默认）：独立 LLM 调用精炼，质量最高但增加延迟（精炼 LLM 串行阻塞）
- **`inline`**：本地正则预清洗（`local_clean_stt()`）+ 聊天 LLM 隐式纠错，一次聊天调用完成，延迟最低
- **`none`**：仅 `local_clean_stt()` 正则清洗，不调用任何 LLM 精炼，零 LLM 开销

### 记忆

`memory_backend` — `"none"`（禁用）`"mem0"`（本地向量库）`"kokoromemo"`（外部服务）

mem0 子配置：

- `[mem0.llm]`：`provider` `base_url` `model` — 记忆提取/摘要用 LLM
- `[mem0.embedder]`：`provider` `model` `embedding_dims` — 向量嵌入模型
- `[mem0.lifecycle]`：`importance_mode` `search_threshold` `search_top_k` `compress_interval` `max_memories_per_user` `importance_min_len`

### 立绘

`portrait_overlay_host` `portrait_overlay_port` `portrait_decision_interval` `portrait_decay_seconds` `portrait_debug_overlay` `portrait_click_through`

详见 [portrait.md](portrait.md)。

### 视觉 / 屏幕识别

`vision_backend` — `"dashscope"` 或 `"ollama"`
`vision_model` — DashScope 默认 `qwen-vl-plus`，Ollama 默认 `qwen2.5vl:3b`
`vision_api_key`

### 主动搭话调度器

全局 `[impulse]` + 四类行为子配置：`[impulse.idle]` `[impulse.recent]` `[impulse.mem]` `[impulse.screen]`

详见 [impulse.md](impulse.md)。

### 屏幕监控 + 记忆事件

`[screen_watch]` 控制周期性截图分析的参数（`watch_interval` `interest_threshold` `vision_timeout`）。

记忆事件配置位于 `[impulse]` 下：`memory_events_enabled` `memory_check_interval` `memory_cooldown_seconds` `memory_date_score` `memory_lookup_score` `memory_lookup_query` `[[impulse.memory_date_events]]`。

详见 [screen_interest.md](screen_interest.md) 和 [memory.md](memory.md)。

### 工具调用 (Tool Calling)

启用后，LLM 可以调用预定义的工具（查看屏幕、搜索记忆、获取时间等），替代旧版正则命令匹配。

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `enabled` | `true` | 总开关。关闭后使用旧版正则命令匹配 |
| `tools` | 全部 5 个工具 | 启用的工具列表 |
| `max_iterations` | `5` | 单次对话中工具调用的最大循环次数，防止死循环 |
| `tool_timeout` | `45.0` | 单次工具调用的超时时间（秒） |

可用工具：
- `look_at_screen` — 截取屏幕截图并分析
- `search_memory` — 搜索长期记忆
- `get_current_time` — 获取当前日期时间
- `get_current_app` — 获取前台窗口信息
- `save_to_memory` — 保存信息到长期记忆

**注意事项：**
- 工具调用会增加 token 用量：每个请求携带工具 schema（约 500-800 tokens），每次工具调用会额外增加一轮 LLM 请求。`max_iterations` 越大，潜在开销越高。
- 小模型（≤3B）对 function calling 支持不稳定。`qwen2.5:1.5b` 容易出现误触发或参数格式错误，建议降级为 `--no-tools` 或只启用 `get_current_time`。
- 可通过 `config.toml` 中的 `[tool_calling]` 块调整，或 CLI 启动时用 `--no-tools` 临时关闭。

### 其他

`kokoromo_url` `kokoromo_dir` — KokoroMemo 外部记忆服务地址和可执行文件路径。仅 `memory_backend = "kokoromemo"` 时使用。
