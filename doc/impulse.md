# 主动搭话

`kokoro/impulse.py` 是主动搭话调度器。当前实现是 LLM planner 维护计划表，而不是简单冲动值累加。

## 生命周期

1. 对话结束或系统空闲。
2. 读取上下文。
3. 调用 planner LLM。
4. planner 输出计划表增删改操作。
5. 执行第一个计划项。
6. 发言结束后重新规划。
7. 用户插话时取消当前计划。

## 规划输入

每次规划会输入：

- 角色 planner system prompt
- 当前时间
- 对话摘要 `session.summary`
- 最近四轮对话
- 长期记忆检索结果
- 屏幕感知缓存
- Edge 当前网页缓存
- 当前计划表
- cognition runtime cache
- emotion state
- 直播模式下的弹幕上下文和用户列表

注意：cognition 输入的是 runtime cache，不是完整 `cognition.json`。

## Edge 页面缓存

如果 `[edge_page_cache].enabled = true`，planner 会看到：

- 标题
- URL
- 抓取时间
- 正文片段

缓存错误也会作为“不可用”状态输入给 planner。

## 计划表

计划表项包含：

- `delay_seconds`
- `action`
- 内部 `id`

planner 输出操作数组，支持：

- `add`
- `delete`
- `modify`

执行后会从计划表移除已执行项。

## 配置

```toml
[impulse]
enabled = true
max_plans = 5
min_plans = 1
planning_model = "deepseek-v4-flash"
screen_timeout = 45
empty_plan_retry_seconds = 10.0
log_plan_table = false
```

## 调试

打开计划表日志：

```toml
[impulse]
log_plan_table = true
```

临时关闭：

```bash
python cli.py --no-impulse
```

## 与文字 CLI 的关系

`text_cli.py` 不启动 impulse。它适合测试角色正常对话表现；`cli.py` 才会测试主动搭话行为。
