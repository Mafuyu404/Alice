# 文字测试 CLI

`text_cli.py` 是专门给人格测试、提示词迭代和自动化评测准备的精简入口。

它不会启动：

- STT
- TTS
- 屏幕识图
- Edge 页面抓取线程
- 直播弹幕
- 立绘
- 字幕
- impulse 主动搭话

它只保留：

- 文字输入
- 文字输出
- ChatSession 上下文组装
- 可选记忆
- 可选 cognition / emotion 更新
- 可选项目内文件工具

## 启动

```bash
python text_cli.py
```

无副作用测试：

```bash
python text_cli.py --no-memory --no-store --no-cognition
```

关闭工具：

```bash
python text_cli.py --no-tools
```

只读工具：

```bash
python text_cli.py --read-only-tools
```

指定角色和模型：

```bash
python text_cli.py --character alice --model deepseek-v4-flash
```

## 参数

| 参数 | 说明 |
| --- | --- |
| `--character` | 角色 ID，默认 `alice` |
| `--model` | 覆盖聊天模型 |
| `--no-memory` | 本次运行禁用记忆后端 |
| `--no-tools` | 禁用项目文件工具 |
| `--read-only-tools` | 文件工具只读，不允许写 |
| `--max-history` | 会话历史消息数 |
| `--no-store` | 不把本轮对话写入记忆 |
| `--no-cognition` | 禁用周期性 cognition 评估 |

## 文件工具

默认启用三个项目内工具：

- `list_project_files`
- `read_project_file`
- `write_project_file`

限制：

- 只能访问项目目录内路径。
- 不能执行命令。
- 不能访问绝对路径。
- 单次读取和写入有长度限制。

这给自动迭代留出了足够空间：智能体可以读角色、提示词和文档，也可以写测试记录、修改 prompt 或角色草案；但不会获得任意 shell 执行能力。

## 自动化输入

可以用管道批量输入：

```bash
printf "请自我介绍\n/usage\n/exit\n" | python text_cli.py --no-memory --no-store
```

PowerShell：

```powershell
@"
请自我介绍
/usage
/exit
"@ | python text_cli.py --no-memory --no-store
```

## 适合的工作流

1. 用 `--no-memory --no-store --no-cognition` 做稳定回归测试。
2. 用 `--read-only-tools` 让模型读取当前提示词和角色文件，但不改文件。
3. 用默认写工具让模型生成候选改动。
4. 人工 review Git diff。
5. 再跑同一批测试输入。
