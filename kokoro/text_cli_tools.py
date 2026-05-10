"""Project-scoped file tools for the text-only test CLI."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MAX_READ_CHARS = 60000
MAX_WRITE_CHARS = 120000


LIST_PROJECT_FILES = {
    "type": "function",
    "function": {
        "name": "list_project_files",
        "description": "列出项目目录内的文件。用于查看可迭代的角色、提示词、配置和文档文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对项目根目录的目录路径，留空表示项目根目录。",
                },
            },
            "required": [],
        },
    },
}


READ_PROJECT_FILE = {
    "type": "function",
    "function": {
        "name": "read_project_file",
        "description": "读取项目目录内的 UTF-8 文本文件内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对项目根目录的文件路径。",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "最多返回多少字符，默认 60000。",
                },
            },
            "required": ["path"],
        },
    },
}


WRITE_PROJECT_FILE = {
    "type": "function",
    "function": {
        "name": "write_project_file",
        "description": "覆盖写入项目目录内的 UTF-8 文本文件。只用于提示词、角色文件、测试记录和文档迭代。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对项目根目录的文件路径。",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的完整文件内容。",
                },
            },
            "required": ["path", "content"],
        },
    },
}


TOOLS = [LIST_PROJECT_FILES, READ_PROJECT_FILE, WRITE_PROJECT_FILE]


class ProjectFileRegistry:
    def __init__(self, allow_write: bool = True):
        self.allow_write = allow_write
        self.tools = TOOLS if allow_write else [LIST_PROJECT_FILES, READ_PROJECT_FILE]

    def enabled_schemas(self) -> list[dict]:
        return self.tools

    def execute(self, name: str, arguments: dict, **_context) -> str:
        try:
            if name == "list_project_files":
                return list_project_files(str(arguments.get("path") or "."))
            if name == "read_project_file":
                return read_project_file(
                    str(arguments.get("path") or ""),
                    max_chars=int(arguments.get("max_chars") or MAX_READ_CHARS),
                )
            if name == "write_project_file":
                if not self.allow_write:
                    return "写入工具未启用。"
                return write_project_file(
                    str(arguments.get("path") or ""),
                    str(arguments.get("content") or ""),
                )
            return f"未知工具：{name}"
        except Exception as exc:
            return f"工具执行失败：{type(exc).__name__}: {exc}"

    def shutdown(self) -> None:
        return


def list_project_files(path_value: str = ".") -> str:
    path = _resolve(path_value)
    if not path.exists():
        return f"路径不存在：{path_value}"
    if not path.is_dir():
        return f"不是目录：{path_value}"

    lines: list[str] = []
    for item in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        rel = item.relative_to(ROOT).as_posix()
        suffix = "/" if item.is_dir() else ""
        lines.append(f"{rel}{suffix}")
    return "\n".join(lines) if lines else "目录为空。"


def read_project_file(path_value: str, max_chars: int = MAX_READ_CHARS) -> str:
    path = _resolve(path_value)
    if not path.exists():
        return f"文件不存在：{path_value}"
    if not path.is_file():
        return f"不是文件：{path_value}"

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "读取失败：该文件不是 UTF-8 文本。"

    max_chars = max(1000, min(max_chars, MAX_READ_CHARS))
    truncated = len(text) > max_chars
    body = text[:max_chars]
    if truncated:
        body += "\n...（已截断）"
    return body


def write_project_file(path_value: str, content: str) -> str:
    if len(content) > MAX_WRITE_CHARS:
        return f"写入失败：内容过长，最多 {MAX_WRITE_CHARS} 字符。"
    path = _resolve(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    rel = path.relative_to(ROOT).as_posix()
    return f"已写入：{rel}（{len(content)} 字符）"


def _resolve(path_value: str) -> Path:
    cleaned = path_value.strip() or "."
    if cleaned in {"/", "\\"}:
        cleaned = "."
    raw = Path(cleaned)
    if raw.is_absolute():
        raise ValueError("只允许使用项目内相对路径。")
    resolved = (ROOT / raw).resolve()
    root = ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("路径越界：只允许访问项目目录内的文件。")
    return resolved
