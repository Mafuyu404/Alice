# VTS 集成

实现文件：
- `kokoro/vts_controller.py`
- `kokoro/vts_body_driver.py`

## 作用

通过 VTube Studio API 控制 Live2D 模型的表情、口型同步和空闲动画。

## 组件

| 组件 | 职责 |
|---|---|
| `VTSController` | VTS API 连接 + 鉴权 + 表情/热点参数查询 |
| `VTSExpressionArbiter` | 多图层参数合成（待机层 + 情绪层 + 脸部脚本 + 身体脚本 + 口型层） |
| `VTSIdleLoop` | 无 TTS 时的周期空闲表情循环 |
| `VTSLipSync` | 基于 TTS 音频能量的口型开合驱动 |
| `VTSBodyDriver` | 低频 LLM 生成脸部/身体脚本，高频本地平滑执行 |

## 表情混合

`VTSExpressionArbiter` 用图层系统管理多路表情输入：

```text
最终参数 = idle
         -> emotion
         -> body_script
         -> face_script
         -> lipsync
         -> tool
```

- `emotion` 层：由情绪层驱动，`session.emotion._on_update` 回调更新
- `idle` 层：空闲动画，由 `VTSIdleLoop` 周期性切换
- `body_script` 层：LLM 生成的身体/头部动作脚本，例如呼吸、摇摆、点头、垂头
- `face_script` 层：LLM 生成的脸部动作脚本，例如嘴角、眼神、眉毛、眨眼
- 口型层：由 `VTSLipSync` 实时驱动
- `tool` 层：显式工具调用覆盖，优先级最高

脸部和身体分开生成，避免“开心=嘴笑+头晃”这类固定组合长期重复造成疲劳。LLM 只生成抽象动作脚本，程序负责 30Hz 插值、过渡、clamp 和参数注入。

## 启动流程

```text
CLI 启动时:
  1. VTSController.authenticate() → 获取 API token
  2. VTSExpressionArbiter.start() → 启动表情混合循环
  3. VTSIdleLoop.start() → 启动空闲动画
  4. VTSBodyDriver.start() → 启动脸部/身体脚本执行器
  5. VTSLipSync 绑定到 tts_engine.on_audio_frame
     → TTS 每输出一帧音频，VTSLipSync.on_audio_frame()
     → 计算 RMS → 设置 mouth_open 参数
```

## 脸部/身体脚本

`VTSBodyDriver` 每隔一小段时间读取内在叙事流、情绪、认知、最近对话、TTS 状态和最近脚本，调用 LLM 输出：

```json
{
  "face": {
    "mood": "轻松开心",
    "energy": 0.6,
    "duration": 3.0,
    "motions": [
      {"target": "mouth", "kind": "smile", "value": 0.45},
      {"target": "eyes", "kind": "look", "x": -0.1, "y": 0.02}
    ]
  },
  "body": {
    "mood": "轻快旁听",
    "energy": 0.5,
    "duration": 3.5,
    "motions": [
      {"target": "body", "kind": "breath", "amplitude": 0.25, "frequency": 0.25},
      {"target": "head", "kind": "sway", "axis": "x", "amplitude": 1.2, "frequency": 0.35}
    ]
  }
}
```

如果 LLM 暂时失败，执行器会使用本地 fallback：呼吸、轻微左右摇摆、眼神漂移和眨眼，避免 Live2D 长时间僵住。

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
