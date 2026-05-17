# 快速开始

## 1. 安装依赖

```bash
pip install requests numpy websockets pywin32 pillow
pip install sherpa-onnx sounddevice
pip install PySide6
pip install mem0ai
```

## 2. 准备模型

如果使用本地记忆：

```bash
ollama pull bge-m3:latest
```

## 3. 配置

最小可用配置：

```toml
llm_url = "http://127.0.0.1:11434"
llm_model = "deepseek-v4-flash"
memory_backend = "mem0"
tts_backend = "minimax"
```

## 4. 运行

文本模式：

```bash
python text_cli.py
```

桌面模式：

```bash
python cli.py
```

多人 watch：

```bash
python run_multi.py --watch --chars alice,penglai
```

## 5. 查看记忆

```bash
python memory_viewer.py
```
