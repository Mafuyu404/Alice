# 配置系统

## 双层配置

框架采用双层配置机制：`config.toml` 作为主配置文件，`config.json` 作为本地密钥覆盖文件（已加入 `.gitignore`）。

### 合并规则

`kokoro/config.py` 中的 `_merge_fallback()` 函数实现合并：

```python
_CONFIG = _merge_fallback(primary=toml_config, fallback=json_config)
```

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

部分密钥也可通过环境变量设置（`config.py` 会优先读取环境变量）：
- `DEEPSEEK_API_KEY`
- `DASHSCOPE_API_KEY`

## 配置项参考

### LLM

```toml
# LLM 地址（兼容 OpenAI 格式）
llm_url = "http://127.0.0.1:11434"
# 默认对话模型
llm_model = "deepseek-v4-flash"
# 可用模型列表（供启动时选择或回退）
available_models = ["qwen2.5:0.5b", "qwen2.5:1.5b", "qwen2.5:7b", "deepseek-v4-flash", "deepseek-v4-pro"]
# DeepSeek API 密钥（环境变量 DEEPSEEK_API_KEY 优先）
deepseek_api_key = ""
# 本地 transformers 后备模型
local_model = "Qwen/Qwen2.5-1.5B-Instruct"
```

### TTS

```toml
tts_backend = "minimax"        # "minimax" 或 "cartesia"

# Cartesia
cartesia_api_key = ""
tts_voice_id = ""
tts_sample_rate = 24000

# MiniMax
minimax_api_key = ""
minimax_model = "speech-2.8-turbo"
minimax_sample_rate = 32000
minimax_tts_speed = 1.05       # 语速倍率
minimax_tts_buffer_seconds = 0.3  # 预缓冲秒数
```

### STT

```toml
stt_model_dir = "models/stt"          # ASR 模型目录
stt_refine_model = "qwen2.5:1.5b"     # 精炼用模型
stt_pause_during_tts = true           # TTS 时暂停麦克风
```

### 记忆

```toml
memory_backend = "mem0"               # "none" | "mem0" | "kokoromemo"
kokoromo_url = "http://127.0.0.1:14514"
kokoromo_dir = "D:/program/kokoromemo"
```

### 立绘

```toml
portrait_overlay_host = "127.0.0.1"
portrait_overlay_port = 17352
portrait_decision_interval = 0.0      # 决策间隔（秒），0=默认
portrait_decay_seconds = 60.0         # 无语音后恢复平静表情的秒数
portrait_debug_overlay = true         # 显示调试信息
portrait_click_through = false        # 鼠标点击穿透
```

### 视觉 / 屏幕识别

```toml
vision_backend = "dashscope"          # "dashscope" 或 "ollama"
vision_model = "qwen-vl-plus"
vision_api_key = ""
```

### 主动搭话调度器

详见 [proactive.md](proactive.md)。

### 屏幕监控

详见 [screen_interest.md](screen_interest.md)。

### Mem0 配置

```toml
[mem0.llm]
provider = "ollama"
base_url = "http://127.0.0.1:11434"
model = "qwen2.5:1.5b"

[mem0.embedder]
provider = "fastembed"
model = "BAAI/bge-small-zh-v1.5"
embedding_dims = 512

[mem0.lifecycle]
importance_mode = "auto"
search_threshold = 0.3
search_top_k = 8
```

## 配置加载流程

1. `kokoro/config.py` 的 `load()` 函数在首次调用时加载
2. 清理所有 HTTP_PROXY 环境变量
3. 读取 `config.toml` 和 `config.json`
4. 合并两层配置
5. 全局缓存 `_CONFIG` 变量，后续调用直接返回

各个配置读取函数（`llm_url()`、`tts_backend()` 等）内部调用 `get()` → `load()`。
