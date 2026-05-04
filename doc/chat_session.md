# 对话会话

## ChatSession

`kokoro/chat_session.py` 管理对话上下文。核心数据结构：

```python
@dataclass
class ChatSession:
    system_prompt: str
    history: list[dict]          # {"role": "user"|"assistant", "content": str}
    max_history: int = 24        # 历史消息上限
    memory_backend: MemoryBackend | None = None
    memory_injection_mode: str = "last"  # "first" | "last"
```

### 关键方法

| 方法 | 功能 |
|------|------|
| `add_message(role, content)` | 添加消息并裁剪超出限制的历史 |
| `get_history()` | 返回当前历史 |
| `build_messages(extra_context=None)` | 构建完整消息列表（system + 记忆 + 历史） |
| `remember(response_text)` | 将 LLM 回复标记为记忆候选 |

### build_messages 行为

1. 从 `memory_backend.search()` 获取相关记忆
2. 构造消息列表：
   - 可选：记忆注入模式 `first`（在 system 后插入）
   - 默认模式 `last`（在历史前插入）
   - 系统提示词
   - 记忆块（格式化为单独消息）
   - 历史消息列表

### 记忆注入模式

- **`last`**（默认）：记忆紧贴对话历史之前，对 LLM 的影响更直接
- **`first`**：记忆在系统提示词之后、历史之前

## LLM 客户端

`kokoro/llm_client.py` 提供 OpenAI 兼容的 API 调用：

```python
async def stream_chat(messages, model=None, system_prompt=None, ...):
    # 调用 LLM API，以 SSE 流返回
    async for chunk in stream_chat(messages, model="deepseek-v4-flash"):
        print(chunk)
```

### 特性

- 深度求索 DeepSeek API 支持（自动识别 `deepseek` 开头的模型名）
- 流式 SSE 解析，逐 token 产出
- 与 ChatSession 配合使用

### API 路由

`config.py` 中的 `api_base()` 决定请求发送地址：
- 当 `memory_backend` 为 `kokoromemo` 且有 `kokoromo_url` 时，使用 `kokoromo_url/v1`
- 否则使用 `llm_url/v1`
