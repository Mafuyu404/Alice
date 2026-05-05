# 对话会话

## ChatSession

`kokoro/chat_session.py` 管理对话上下文。`@dataclass` 定义：

```python
@dataclass
class ChatSession:
    character_id: str
    character_data: dict
    memory_backend: object
    max_history: int = 20            # 历史消息上限（双向，实际最多 2× 条）
    history: list[dict]              # [{"role":"user"|"assistant","content":str}, ...]
    screen_contexts: list[str]       # 最近屏幕观察记录（最多 3 条）
    max_screen_contexts: int = 3
```

### 属性

| 属性 | 返回 | 说明 |
|------|------|------|
| `character_name` | `str` | 角色名称 |
| `system_prompt` | `str` | 角色系统提示词（自动拼接 template + calibration） |

### 关键方法

| 方法 | 功能 |
|------|------|
| `add_screen_context(content)` | 添加一条屏幕观察记录，自动裁剪到最近 max_screen_contexts 条 |
| `build_messages(user_text, include_screen=True, extra_context=None)` | 构建 LLM 请求消息列表 |
| `remember(user_text, assistant_text, async_store=True)` | 存入历史 + 异步写入记忆后端 |

### build_messages 行为

按以下顺序组装消息列表：

1. **system: 角色系统提示词** （`character_system.template` + `expression_calibration`）
2. **system: 额外上下文** （`extra_context` 参数，用于指令结果等）
3. **system: 最近屏幕观察** （如果 `include_screen=True` 且有记录，格式化为"以下是你最近通过屏幕识别观察到的用户活动……"）
4. **system: 记忆上下文** （从 `memory_backend.get_context()` 获取相关记忆）
5. **历史消息** （`self.history`，记录用户和角色的过往对话）
6. **user: 当前用户输入**

### remember 行为

- 将 `(user_text, assistant_text)` 追加到 `history`
- 超出 `max_history × 2` 条时裁剪最早的历史
- 如果 `async_store=True`（默认），在后台线程调用 `memory_backend.store()` 写入长期记忆

## LLM 客户端

`kokoro/llm_client.py` 提供 OpenAI 兼容 API 的同步流式调用：

```python
for chunk in stream_chat(messages, model="deepseek-v4-flash"):
    print(chunk)  # 逐 token 产出
```

### 特性

- **DeepSeek 自动路由**：模型名以 `deepseek` 开头时自动使用 DeepSeek 云端 API + API Key
- **SSE 流式解析**：逐 token 产出，首 token 延迟可低至 0.5 秒
- **超时控制**：`timeout` 参数（默认 120 秒）
- **同步生成器**：非 async，可直接在普通线程中使用

### 辅助函数

| 函数 | 功能 |
|------|------|
| `api_base_for(model)` | 获取模型对应的 API base URL（含 `/v1`） |
| `upstream_url_for(model)` | 获取模型对应的上游地址（不含 `/v1`） |
| `build_payload(model, messages, stream)` | 构建请求体 |
| `parse_sse_delta(line)` | 解析 SSE 行，提取 content delta |

### API 路由规则

```
model 以 deepseek 开头 → DeepSeek API (api.deepseek.com)
memory_backend = kokoromemo 且有 kokoromo_url → kokoromo_url/v1
否则 → llm_url/v1
```
