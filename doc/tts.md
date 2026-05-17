# TTS

支持后端：

- MiniMax
- Cartesia

## 当前主线

- 多角色模式下 TTS 串行播放
- 当前一句播完后再播下一句
- 预取 followup 允许提前生成，但不能打断正在播放的上一句

## 相关文件

- `kokoro/tts_minimax.py`
- `kokoro/tts_cartesia.py`
