# 角色系统

角色按目录组织：

```text
characters/{character_id}/
  {character_id}.json
  config.toml
  cognition.json
  emotion.json
  portrait/
    portrait.json
    *.png
```

只有 `{character_id}.json` 是必需的。其他文件按功能需要存在。

## 主角色文件

示例：

```json
{
  "name": "爱丽丝·玛格特罗伊德",
  "description": "...",
  "personality": "...",
  "background": "...",
  "relationship": "...",
  "speaking_style": "...",
  "greeting": "..."
}
```

`kokoro.character.build_system_prompt()` 会把这些字段组装成角色 system prompt。

## 角色私有配置

`characters/{id}/config.toml` 可以覆盖部分运行配置，例如：

```toml
llm_model = "charglm-4"
llm_url = "http://127.0.0.1:8000/v1"
```

不要把 API key 放进角色目录。密钥应放在根目录 `config.json` 或环境变量。

## Cognition

`cognition.json` 存完整认知条目：

```json
{
  "entries": {
    "用户": "对用户的长期认知",
    "自己和用户的关系": "关系锚点"
  }
}
```

运行时不会把整个文件塞进 prompt，而是维护一个 runtime cache，只注入相关子集。

## Emotion

`emotion.json` 存当前情绪：

```json
{
  "tone": "平静但有一点担心",
  "motivation": "想确认用户有没有休息"
}
```

为空时不注入。

## 立绘

`portrait/portrait.json` 是数组：

```json
[
  {
    "id": "neutral.png",
    "notes": "平静、正面、适合普通倾听"
  }
]
```

`id` 必须对应同目录下的图片文件。`notes` 会交给立绘选择模型。

## 新建角色

1. 创建目录 `characters/my_character/`
2. 创建 `characters/my_character/my_character.json`
3. 可选添加 `config.toml`
4. 可选添加 `cognition.json` 和 `emotion.json`
5. 可选添加 `portrait/`
6. 启动：

```bash
python text_cli.py --character my_character
python cli.py --character my_character
```
