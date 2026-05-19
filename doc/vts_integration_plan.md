# VTS 集成

实现文件：`kokoro/vts_controller.py`

## 作用

通过 VTube Studio API 控制 Live2D 模型的表情、口型同步和空闲动画。

## 组件

| 组件 | 职责 |
|---|---|
| `VTSController` | VTS API 连接 + 鉴权 + 表情/热点参数查询 |
| `VTSExpressionArbiter` | 多图层表情混合（情绪层 + 空闲层 + 口型层） |
| `VTSIdleLoop` | 无 TTS 时的周期空闲表情循环 |
| `VTSLipSync` | 基于 TTS 音频能量的口型开合驱动 |

## 表情混合

`VTSExpressionArbiter` 用图层系统管理多路表情输入：

```text
表情最终参数 = emotion_layer * 1.0
             + idle_layer * 0.3
             + mouth_open * 1.0
```

- `emotion` 层：由情绪层驱动，`session.emotion._on_update` 回调更新
- `idle` 层：空闲动画，由 `VTSIdleLoop` 周期性切换
- 口型层：由 `VTSLipSync` 实时驱动

## 启动流程

```text
CLI 启动时:
  1. VTSController.authenticate() → 获取 API token
  2. VTSExpressionArbiter.start() → 启动表情混合循环
  3. VTSIdleLoop.start() → 启动空闲动画
  4. VTSLipSync 绑定到 tts_engine.on_audio_frame
     → TTS 每输出一帧音频，VTSLipSync.on_audio_frame()
     → 计算 RMS → 设置 mouth_open 参数
```

## 口型同步

`VTSLipSync` 分析 TTS 音频帧的能量：
- 通过 `tts_engine.on_audio_frame` 接收原始音频
- 每帧计算 RMS → 映射到 mouth_open 参数（0.0 ~ 1.0）
- TTS 停止后自动关闭口型

## 多角色

当前 VTS 集成仅支持单角色。多角色场景中尚未实现 VTS 实例切换。

## 依赖

- VTube Studio（Windows 桌面应用）
- VTube Studio API（默认端口 8001）
- 已配置好的 Live2D 模型（含表情和热点参数）
