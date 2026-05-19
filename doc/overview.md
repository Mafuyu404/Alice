# 架构概览

Alice Chat 当前结构分为五层：

1. 入口层
- `cli.py`
- `text_cli.py`
- `run_multi.py`

2. 会话层
- `kokoro/chat_session.py`
- 管理单角色的系统提示、历史、记忆、认知和 inner stream。

3. 调度层
- `kokoro/dialogue_orchestrator.py`
- `kokoro/multi_chat.py`
- 决定谁说、何时说、是否沉默、是否使用屏幕/网页上下文。
- 单人语音模式下，Dialogue 也负责 STT 池的发言时机判断、用户内容提炼和回复生成。

4. 感知与输出层
- STT / TTS
- 立绘 / 字幕
- AEC / denoise
- 屏幕兴趣度 / Edge 页面缓存

5. 记忆与人格层
- `kokoro/memory.py`
- `kokoro/memory_events.py`
- cognition
- inner stream

## 语音输入链路

单人语音模式不再依赖“截句推送”。STT 文本先进入池，明显停顿后由 Dialogue LLM 判断：

- `wait`：用户可能没说完，继续等。
- `backchannel`：轻回应。
- `speak`：正式接话。

这样可以处理半句话、停顿、改口、数数测试和连续话题。

## 长期记忆

当前长期记忆推荐链路：
- `mem0`
- `Ollama`
- `bge-m3:latest`
- 本地 qdrant

## 屏幕与网页

随机 MC 页面场景使用网页缓存驱动，不依赖角色编造页面内容。
