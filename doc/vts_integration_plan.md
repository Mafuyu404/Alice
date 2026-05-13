# AI-Driven VTS Live2D Control — 集成方案

## 1. 概述

目标：让 AI 角色在对话中通过 VTube Studio API 驱动 Live2D 模型，实现自然的面部表情和唇形同步。

控制路径分为四层，**并行工作，仲裁合并**：

```
LLM (function calling)
  ──→ Tool: vts_expression()       ← 显式表情指令（最高优先级）
Emotion System (emotion.py)
  ──→ tone → expression mapping     ← 情绪驱动表情（中优先级）
TTS Engine (StreamingTTS)
  ──→ audio energy → MouthOpen     ← 唇形同步（说话期间覆盖）
Idle Loop (定时器)
  ──→ blink / breathe / sway       ← 待机动画（最低优先级）
```

## 2. 四层控制详解

### 层 1：Idle Layer — 待机动画

让模型在无对话时保持"活着"的感觉。

| 动作 | 参数 | 周期 | 实现方式 |
|------|------|------|----------|
| 眨眼 | `EyeOpenLeft/Right: 1→0→1` | 3-6s 随机间隔 | 闭眼 120ms 后恢复 |
| 呼吸 | `FacePositionZ: sin(t) * amp` | 4s 周期 | 正弦波叠加 |
| 微转头 | `FaceAngleX: sin(t*0.7) * 0.5°` | ~9s 周期 | 慢速漂移 |
| 嘴微动 | `MouthOpen: 0↔0.03` | 伴呼吸 | 极轻微 |

- 由后台 `asyncio.Task` 驱动，主循环每 ~100ms 注入一次
- 在 TTS 播放时暂停（让位给唇形同步）
- 眨眼周期仅在眼睛参数未被上层覆盖时生效

### 层 2：Emotion Layer — 情绪驱动表情

将 `emotion.EmotionState`（情绪基调 + 中期动机）映射为面部参数。

**映射表**（默认值，角色可覆写）：

```
neutral    → MouthSmile=0, Brows=0.5, EyeOpen=1
happy      → MouthSmile=0.7, Brows=0.3
sad        → MouthSmile=0, Brows=0.7, MouthOpen=0.1
angry      → Brows=1, MouthSmile=0, MouthOpen=0.15
surprised  → EyeOpen=1, MouthOpen=0.6, Brows=0.8
tired      → EyeOpen=0.5, MouthSmile=0, Brows=0.3
thinking   → EyeLeftX=0.15, Brows=0.5 （微微侧目）
shy        → MouthSmile=0.3, Brows=0.6, EyeOpen=0.8
excited    → MouthSmile=0.9, EyeOpen=1, Brows=0.2
sad        → MouthSmile=0, Brows=0.8, EyeOpen=0.7, MouthOpen=0.05
```

**触发条件**：
- 在 `emotion.evaluate()` 完成后，检查新的 `tone` 值
- 通过中文关键词匹配（"开心"→happy，"难过"→sad 等）
- 过渡：0.3s 渐变（多个帧逐步调整 weight）
- 静默 60s 后自动衰减到 neutral

### 层 3：LLM Tool Layer — 对话级显式表达

通过 OpenAI 兼容的 function calling 向 LLM 暴露 `vts_expression` 工具。

**Schema**（定义在 `tool_schemas.py`）：

```json
{
  "name": "vts_expression",
  "description": "控制角色Live2D面部表情。在语气需要配合特定表情时使用，例如微笑、挑眉、撇嘴、瞪眼等。每轮最多调用一次。",
  "parameters": {
    "expression": {
      "enum": ["smile","happy","sad","angry","surprised","tired",
               "thinking","shy","excited","awkward","wink","pout",
               "sigh","cry","doubt","neutral"]
    },
    "intensity": {"type": "number", "min": 0, "max": 1},
    "duration_seconds": {"type": "number", "min": 0, "max": 30}
  }
}
```

**Handler**（定义在 `tool_handlers.py`）：
- 调用 `VTSController.set_expression(expression, intensity)`
- 启动定时器在 `duration_seconds` 后恢复为 emotional layer 的值
- 返回简短确认（不影响 LLM 回复流）

调用时机举例：
- 用户："你今天心情很好？" → LLM 调 `vts_expression("happy")` 再回复
- AI 说俏皮话 → 调 `vts_expression("wink", duration=1.5)`
- AI 被用户的问题难住 → 调 `vts_expression("thinking")`

**不要求每轮都调用**。日常对话由 Emotion Layer 自动处理表情。Tool 调用仅用于 LLM 觉得"此处需要刻意表达"的场合。LLM 的 `tool_choice` 保持 `auto`，由模型自行判断是否需要调用。

### 层 4：TTS Lip-Sync — 唇形同步（说话时）

这是用户特别强调的关键需求。利用现有的 `StreamingTTS.on_audio_frame` 回调实现。

**方案：实时音频能量 → MouthOpen**

```
TTS Engine → audio PCM chunk
                  ↓
          RMS energy 计算
                  ↓
          energy → MouthOpen 映射
          (smooth 滤波防抖动)
                  ↓
          VTSController.inject()
```

**具体步骤**：

1. `StreamingTTS` 在 `_play_worker` 中每播放一个 audio chunk 前调用 `on_audio_frame(chunk)`
2. `VTSLipSync` 监听此回调，计算 chunk 的 RMS：
   ```python
   rms = np.sqrt(np.mean(chunk**2))
   mouth = np.clip(rms * 3.0, 0.0, 1.0)  # 乘系数放大
   ```
3. 低通滤波（EMA）避免帧级抖动：
   ```python
   smoothed = smoothed * 0.7 + mouth * 0.3
   ```
4. 以 ~20-30Hz 频率注入 `MouthOpen` + 轻微 `MouthSmile`
5. TTS 停止后，`MouthOpen` 归零

**后备方案**（如果音频能量不可靠）：
- TTS 开始事件 → `MouthOpen=0.25`（固定半开）
- TTS 结束事件 → `MouthOpen=0`
- 虽然不是逐帧同步，但比无唇动好得多

**集成到现有架构**：
```python
# cli.py 或 agent_loop.py 中：
tts = StreamingTTS()
vts_lipsync = VTSLipSync(vts_controller)
tts.on_audio_frame = vts_lipsync.on_audio_frame

# 状态机联动：
# TTS_START → vts_lipsync.start()
# TTS_DONE  → vts_lipsync.stop()
```

**重要**：唇形同步期间暂停 Idle Layer 的 MouthOpen 控制，避免冲突。

## 3. 仲裁机制（Expression Arbiter）

四层控制可能同时发出指令。需要一个简单的仲裁器来决定最终注入值。

**优先级规则**（从高到低）：

```
LLM Tool (显式表情)   >   TTS Lip-Sync (说话)   >   Emotion Layer (情绪)   >   Idle Layer (待机)
```

**仲裁逻辑**：
1. 如果 TTS 正在播放 → MouthOpen 由 Lip-Sync 控制，其他参数由 Emotion/Tool 控制
2. 如果 Tool 层有活跃表情（duration 内）→ 所有参数由 Tool 覆盖
3. 否则使用 Emotion Layer 映射
4. Idle Layer 仅填充未被其他层指定的参数（眨眼、呼吸）

**实现**：`VTSExpressionArbiter` 维护一个参数合并器，每秒调用 10 次 `inject()`。
每个层级写入自己的参数 dict，仲裁器合并后发送。

```
合并策略示例（优先级叠加）：
  Idle:     {EyeOpenLeft: cycle, FacePositionZ: sin}
  Emotion:  {MouthSmile: 0.7, Brows: 0.3}
  TTS:      {MouthOpen: 0.5}              ← 只覆盖 MouthOpen
  Tool:     {}                             ← 无活跃显式表情
  ─────────────────────────────────────────
  Result:   {EyeOpenLeft: cycle, FacePositionZ: sin,
             MouthSmile: 0.7, Brows: 0.3, MouthOpen: 0.5}
```

## 4. 文件/数据结构

### 角色自定义映射：`characters/{id}/vts_mapping.json`

```json
{
  "expressions": {
    "neutral":   {"MouthSmile": 0, "Brows": 0.5, "EyeOpenLeft": 1, "EyeOpenRight": 1},
    "happy":     {"MouthSmile": 0.7, "Brows": 0.3},
    "sad":       {"MouthSmile": 0, "Brows": 0.8, "MouthOpen": 0.1},
    "angry":     {"Brows": 1, "MouthSmile": 0, "MouthOpen": 0.15},
    "surprised": {"EyeOpenLeft": 1, "EyeOpenRight": 1, "MouthOpen": 0.6, "Brows": 0.8},
    "tired":     {"EyeOpenLeft": 0.5, "EyeOpenRight": 0.5, "MouthSmile": 0, "Brows": 0.3},
    "thinking":  {"EyeLeftX": 0.15, "Brows": 0.5},
    "shy":       {"MouthSmile": 0.3, "Brows": 0.6, "EyeOpenLeft": 0.8, "EyeOpenRight": 0.8},
    "excited":   {"MouthSmile": 0.9, "EyeOpenLeft": 1, "EyeOpenRight": 1, "Brows": 0.2}
  },
  "idle": {
    "blink_interval_min": 3.0,
    "blink_interval_max": 6.0,
    "blink_speed": 0.12,
    "breathing_amplitude": 0.3,
    "head_sway_amplitude": 1.0
  },
  "emotion_keywords": {
    "开心": "happy", "高兴": "happy", "快乐": "happy",
    "难过": "sad", "伤心": "sad",
    "生气": "angry", "烦": "angry",
    "惊讶": "surprised", "震惊": "surprised",
    "疲惫": "tired", "累": "tired",
    "思考": "thinking",
    "害羞": "shy",
    "兴奋": "excited"
  },
  "lipsync": {
    "enabled": true,
    "energy_multiplier": 3.0,
    "smooth_factor": 0.7,
    "mouth_open_max": 0.9,
    "mouth_smile_amount": 0.15
  }
}
```

## 5. 集成点汇总

| 模块 | 集成方式 | 文件 |
|------|----------|------|
| `VTSController` | 已有，扩增 expression + lipsync 方法 | `kokoro/vts_controller.py` |
| `VTSExpressionArbiter` | 新增，四层参数合并 | `kokoro/vts_controller.py` |
| `VTSLipSync` | 新增，audio→MouthOpen | `kokoro/vts_controller.py` |
| `emotion.py` | `evaluate()` 成功后触发表情更新 | `kokoro/emotion.py` |
| `tool_schemas.py` | 注册 `vts_expression` 工具定义 | `kokoro/tool_schemas.py` |
| `tool_handlers.py` | 注册 `handle_vts_expression` | `kokoro/tool_handlers.py` |
| `tool_registry.py` | 注册 handler | `kokoro/tool_registry.py` |
| `tts_minimax.py` | 设置 `on_audio_frame` 回调 | `kokoro/tts_minimax.py` |
| `state_machine.py` | 新增 `VTSState` 可选项 | `kokoro/state_machine.py` |
| `cli.py` | 初始化 VTS + 启动 idle loop | `cli.py` |
| `config.py` | 读取 vts 配置段 | `kokoro/config.py` |
| `prompts.json` | 添加 `vts_expression` 工具使用说明 | `prompts.json` |

## 6. 实现步骤

### Step 1：VTSController 增强
- 添加 `set_expression(expression_id, intensity=1.0)` 方法
- 添加 `set_expression_from_map(expression_map)` 方法
- 添加 `load_mapping(character_id)` 方法
- 添加 lipsync `on_audio_frame` 处理

### Step 2：Idle Layer（后台循环） — 新文件 `kokoro/vts_idle.py`
- `VTSIdleLoop` 类，asyncio task
- 眨眼循环（随机间隔）
- 呼吸正弦波
- 监听 TTS 状态暂停

### Step 3：Emotion 集成
- `emotion.py` 中 evaluate() 成功后触发 VTS 表情更新
- `VTSController.set_emotion(tone)` 映射 tone→expression

### Step 4：Lip-Sync — `kokoro/vts_lipsync.py`
- 接收 `on_audio_frame` 回调
- RMS 计算 → MouthOpen 映射
- TTS start/stop 事件处理
- EMA 平滑滤波

### Step 5：Expression Arbiter
- 合并四层控制值
- 优先级仲裁
- ~10Hz 定时注入

### Step 6：LLM Tool
- 注册 `vts_expression` function calling
- Handler 实现：调 `VTSController.set_expression()` + auto-revert

### Step 7：集成到主流程
- `cli.py` / `agent_loop.py` 初始化 VTS + Idle + LipSync
- 连接状态机事件
- 配置项：`config.toml` 添加 `[vts]` 段

## 7. 配置项

```toml
# config.toml
[vts]
enabled = true
host = "localhost"
port = 8001
idle_blink = true
idle_breathing = true
emotion_auto_expression = true
lipsync_enabled = true
lipsync_method = "audio_energy"  # "audio_energy" | "event"
```

## 8. 注意事项

1. **VTS 追踪冲突**：如果 VTS 的摄像头/iPhone 追踪开启，它会持续覆盖我们注入的 tracking 参数。方案：
   - 在 VTS 中禁用面部追踪，完全由程序控制
   - 或使用 `mode="add"` 在追踪基础上叠加
   - 或使用 `weight=1` 完全覆盖

2. **性能**：WebSocket 注入频率控制在 ~10Hz 以内，避免 VTS 过载

3. **容错**：VTS 断开时自动重连，静默降级（仅禁 VTS 相关功能，不影响对话）

4. **唇形同步精度**：音频能量映射 MouthOpen 是近似方案。如果后续需要更精确的 viseme 级同步，可以对接 MiniMax TTS 返回的 phoneme 时间戳。
