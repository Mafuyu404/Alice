# 自主系统 Roadmap

路线目标：把现有对话中心路径收束为生命周期架构：

```text
信息事件 → 内在叙事流 → 行动能力 → 行动结果 → 信息事件
```

## 阶段 1：统一事件回流

- 所有外部输入都进入 `InputEventBus`。
- 所有自身行动也回写事件：说话、沉默、搜索、观察、记忆写入、失败、等待。
- 工具结果统一为 `action_result` 或同等事件，而不是旧回复的附属内容。

验收：从事件日志能复盘“她感知到了什么、做了什么、结果如何”。

## 阶段 2：行动能力抽象

- 定义统一 `ActionBatch` / `Action` 结构：`cycle_id/causality_id/actions[]` 和 `action_id/action/reason/args/mode/visibility/result_policy`。
- 将 `say`、`wait`、`search_web`、`observe_screen`、`write_memory`、`update_cognition` 等都视为能力。
- 给慢行动加后台执行和结果回写。
- 并行行动结果按 `cycle_id/action_id/causality_id` 回写，并通过短窗口合并后再触发内在叙事流。

验收：说话和看屏幕走同一套行动记录与结果回流语义；多个并行工具完成时不会连续刷多次内在叙事流。

## 阶段 3：行动选择层

- 将 `autonomous_step` 扩展为通用 `ActionPolicy`。
- 行动选择优先读取内在叙事流、近期事件、可用能力和安全边界。
- `dialogue_orchestrator` 降级为实时语音快速路径，最终可并入 `ActionPolicy`。

验收：无人说话时系统也能自然选择等待、观察、搜索、整理记忆或主动说话。

## 阶段 4：经验沉淀

- `memory_events` 接收外部输入和自身行动组成的完整经历。
- `inner_memory` 只沉淀“发生过的一整件事”，不存流水账。
- `inner_cognition` 从反复经历中更新稳定认知。

验收：角色能记得自己查过什么、为什么查、查完后理解如何变化。

## 阶段 5：长期运行质量

- 行动频率、LLM 调用和后台任务有预算。
- 慢行动可取消、过期或降级为观察。
- 崩溃后能恢复 inner stream、近期事件和未完成行动。

验收：挂一整天不会刷爆 token，也不会因为旧事件反复说话。

## 当前优先级

阶段 1-2 的骨架已经落地：

- `ActionBatch` / `Action` 数据结构已存在。
- 内在叙事流后的 `AutonomousStep` 已走批次决策和并行执行。
- `observe_screen`、`search_web`、`write_memory`、`update_cognition` 已作为行动能力接入。
- QQ 自主参与已从批次中提取 `say_qq` / `send_sticker` 公开行动，同时并行执行后台行动。
- 旧 agent tool-call 链路会把工具结果回写为 `action_result`。

下一步重点是把实时语音快速路径也逐步并入统一 `ActionPolicy`，并完善批次状态表、过期/取消策略和长期任务完成回流。
