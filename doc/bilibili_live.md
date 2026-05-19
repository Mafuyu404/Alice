# Bilibili 直播弹幕

实现文件：`kokoro/bilibili_live.py`

## 作用

通过 Bilibili 直播弹幕 WebSocket 协议接收直播间弹幕，作为场景输入的一部分供调度器使用。

## 工作流程

```text
BilibiliLiveManager
  │
  ├─ 连接 Bilibili 直播 WebSocket (room_id)
  │
  ├─ 接收弹幕消息
  │   ├─ 去重
  │   └─ 写入弹幕缓冲区 (buffer_max_age = 60s)
  │
  └─ get_danmaku_context(max_entries=40)
       └─ 返回最近弹幕文本（供 planner 使用）
```

## 与对话调度器的关系

Bilibili 直播场景中，弹幕作为 planner 的输入材料：

```text
impulse.planner_user 中注入：
  1. 最近弹幕列表（get_danmaku_context）
  2. 观众发言频率统计（get_user_summaries）

impulse.trigger_system 中注入：
  直播场景提示词（live_system_hint）+ 弹幕上下文

不再使用旧版的 direct_reply 模式——所有弹幕回应通过对话调度器决策。
```

## 场景配置

```toml
# 连接开关
bilibili_live.enabled = true

# 场景理解开关（决定 LLM 如何理解输入）
[scene]
live_enabled = true      # 按直播场景理解
multi_enabled = false    # 单人直播
```

`live_enabled = true` 时，角色会理解"对方可能是在跟弹幕说话，不一定直接对自己说"。

## 未来方向

- 弹幕发送者作为独立对象进入记忆与认知体系
- 不同观众在 cognition 中有独立条目
- 不再把所有外部发言压缩成泛称"观众"
