# Bilibili 直播弹幕

`kokoro/bilibili_live.py` 维护 Bilibili 直播间弹幕连接，把弹幕保存在短期缓冲区。`impulse` 在直播模式下读取这些弹幕，决定是否主动回复。

## 配置

```toml
[bilibili_live]
enabled = false
live_mode = true
room_id = 0
buffer_max_age = 60.0
reconnect_delay = 5.0
```

## 启动

配置房间号：

```toml
[bilibili_live]
enabled = true
live_mode = true
room_id = 22632424
```

或命令行覆盖房间号：

```bash
python cli.py --bilibili-room 22632424
```

如果配置中 `enabled = false`，即使命令行传了房间号也不会连接。

## 与 impulse 的关系

直播弹幕不会直接触发回复。它进入弹幕缓冲区后，由 `impulse` planner 在空闲时综合判断：

- 当前对话状态
- 弹幕内容
- 用户列表
- 记忆
- cognition
- emotion
- 当前计划表

然后生成或修改计划项。

## Cognition

直播模式下，频繁互动的观众可能进入 cognition 评估。目标不是记录流水账，而是提炼观众特点、兴趣和说话风格。

## 关闭

```toml
[bilibili_live]
enabled = false
```

或运行时不用完整 CLI。`text_cli.py` 不启动直播连接。
