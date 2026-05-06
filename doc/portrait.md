# 立绘系统

立绘系统由两部分组成：

- `overlay_slideshow.py`：PySide6 透明窗口，显示 PNG 立绘，并提供本地 HTTP 控制接口。
- `kokoro/portrait_controller.py`：CLI 中的控制器，启动立绘窗口，并让 LLM 根据对话选择合适差分。

## 文件布局

每个角色有自己的立绘目录：

```text
characters/{character_id}/portrait/
├── portrait.json
└── *.png
```

当前主流程不再使用根目录 `img/` 作为角色立绘目录。`overlay_slideshow.py --image-dir img` 仍可用于手动测试旧目录，但 CLI 会传入 `characters/{character_id}/portrait`。

## portrait.json

`portrait.json` 是立绘候选表。当前推荐格式是数组，每项只保留 `id` 和 `notes`：

```json
[
  {
    "id": "penglai_seated_hands_lap_quiet_neutral_p01.png",
    "notes": "正坐合手放在膝前，直视前方，眼神空灵，嘴角轻收。"
  }
]
```

规则：

- `id` 必须是同目录下真实存在的 PNG 文件名。
- `notes` 是给立绘选择 LLM 看的中文描述，应描述眼神、嘴型、表情强度和手势姿态。
- 文件名建议包含角色、姿态、表情和原始序号，例如 `penglai_seated_hands_lap_tiny_surprise_p06.png`。
- 保留 `pXX` 有助于追溯原始差分顺序。

`overlay_slideshow.py` 兼容旧格式：如果条目里有 `new_name`，会优先使用 `new_name`；否则使用 `id`。旧版 `series`、`emotion`、`pose`、`eyes`、`mouth` 字段仍可被 HTTP 过滤接口读取，但不是必需字段。

## 立绘选择

CLI 启动立绘时：

1. `kokoro.portrait_controller.create_controller(character_id, model)` 创建客户端和后台选择线程。
2. 客户端启动 `overlay_slideshow.py --image-dir characters/{character_id}/portrait`。
3. 选择线程读取 `characters/{character_id}/portrait/portrait.json`。
4. 每轮助手回复结束后，线程把用户输入、助手回复、当前立绘和候选表交给 LLM。
5. LLM 只需返回候选 `id`，控制器通过 HTTP 切换图片。
6. 长时间无对话后，控制器会尝试回到文件名包含 `neutral` 的立绘。

相关配置：

```toml
portrait_overlay_host = "127.0.0.1"
portrait_overlay_port = 17352
portrait_decision_interval = 0.0
portrait_decay_seconds = 60.0
portrait_debug_overlay = false
portrait_click_through = false
```

`portrait_decision_interval = 0.0` 在当前代码中会作为 0 秒间隔传入，实际循环仍有最小等待；想减少立绘选择频率可以设为 `2.0` 或更高。

## 手动启动

```bash
python overlay_slideshow.py --image-dir characters/penglai/portrait
python overlay_slideshow.py --host 127.0.0.1 --port 17352 --image-dir characters/alice/portrait
```

窗口行为：

- 左键拖动：移动窗口
- 鼠标滚轮：缩放，范围 0.2x 到 4.0x
- `F8`：切换鼠标点击穿透
- `Space`：播放/暂停轮播
- `Right`：下一张
- `Left`：上一张
- `Esc`：退出

窗口位置和缩放保存到根目录 `portrait_overlay_state.json`。

## HTTP API

默认地址：`http://127.0.0.1:17352`

| 接口 | 方法 | 作用 |
| --- | --- | --- |
| `/health` | GET | 健康检查 |
| `/status` | GET | 当前立绘、系列列表和总数 |
| `/portraits` | GET | 列出立绘，可按 query 过滤 |
| `/control` | POST | 切换、播放、暂停、点击穿透、退出 |
| `/debug` | POST | 更新调试覆盖层数据 |

示例：

```bash
curl http://127.0.0.1:17352/health
curl http://127.0.0.1:17352/status
curl http://127.0.0.1:17352/portraits
curl -X POST http://127.0.0.1:17352/control ^
  -H "Content-Type: application/json" ^
  -d "{\"action\":\"show\",\"name\":\"penglai_seated_hands_lap_tiny_surprise_p06.png\"}"
```

`/control` 支持：

| action | 参数 | 作用 |
| --- | --- | --- |
| `show` | `name` | 显示指定文件 |
| `show` | `random: true` | 从候选中随机显示 |
| `pause` | 无 | 暂停轮播 |
| `play` | 无 | 恢复轮播 |
| `click_through` | `enabled: true/false` | 设置点击穿透 |
| `shutdown` | 无 | 关闭立绘窗口 |

## 当前素材

| 角色 | 目录 | 数量 | 说明 |
| --- | --- | --- | --- |
| `alice` | `characters/alice/portrait` | 96 | 已整理为 `id` / `notes` |
| `penglai` | `characters/penglai/portrait` | 7 | 已整理为 `id` / `notes` |
| `yuki` | `characters/yuki/portrait` | 0 | 暂无 PNG |

## 描述建议

`notes` 不宜只写“开心”“生气”这类宽泛词。更好的描述应包含：

- 眼睛：睁大、半垂、闭眼、侧目、直视
- 嘴型：抿嘴、微张、小圆口、浅笑、露齿笑
- 姿态：手按胸前、指向、双手交叠、正坐合手
- 情绪强度：轻微惊讶、克制不满、含蓄高兴、强压火气

保持一句话即可，方便 LLM 快速比较候选。
