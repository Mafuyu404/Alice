# 架构概览

Alice Chat 的当前结构分为五层：

1. 入口层  
   - `cli.py`
   - `text_cli.py`
   - `run_multi.py`

2. 会话层  
   - `kokoro/chat_session.py`
   - 管理单角色的系统提示、历史、记忆、认知、情绪

3. 调度层  
   - `kokoro/dialogue_orchestrator.py`
   - `kokoro/multi_chat.py`
   - 决定谁说、何时说、是否沉默、是否使用屏幕/网页上下文

4. 感知与输出层  
   - STT / TTS
   - 立绘 / 字幕
   - 屏幕兴趣度 / Edge 页面缓存

5. 记忆与人格层  
   - `kokoro/memory.py`
   - `kokoro/memory_events.py`
   - cognition / emotion

当前长期记忆推荐链路：

- `mem0`
- `Ollama`
- `bge-m3:latest`
- 本地 qdrant

当前随机 MC 页面场景使用网页缓存驱动，不依赖角色编造页面内容。
