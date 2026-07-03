"""QQ media logging, prompt, and JSON parsing helpers."""

from __future__ import annotations

import json
import re
import sys


def _debug_log(event: str, **fields) -> None:
    parts = [f"[qq_image] {event}"]
    for key, value in fields.items():
        text = str(value)
        parts.append(f"{key}={text!r}")
    try:
        print(" ".join(parts), flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe = " ".join(parts).encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe, flush=True)


def _clip(text: str, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _fill_prompt(template: str, values: dict[str, str]) -> str:
    text = str(template or "")
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def _extract_json(text: str) -> dict | None:
    raw = str(text or "").strip().lstrip("\ufeff")
    if not raw:
        return None
    if "</think>" in raw:
        raw = raw.split("</think>")[-1].strip()
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    for match in re.finditer(r"```(?:json|JSON)?\s*\n?(.*?)```", raw, re.S):
        block = match.group(1).strip()
        try:
            value = json.loads(block)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            nested = _parse_json_object_slice(block)
            if nested:
                return nested
            salvaged = _salvage_json_fields(block)
            if salvaged:
                return salvaged
    return _parse_json_object_slice(raw) or _salvage_json_fields(raw)


def _parse_json_object_slice(text: str) -> dict | None:
    start = str(text or "").find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return value if isinstance(value, dict) else None
    return None


def _salvage_json_fields(text: str) -> dict | None:
    raw = str(text or "")
    keys = (
        "kind",
        "description",
        "text",
        "tone",
        "context_meaning",
        "save",
        "reason",
        "desc",
        "intensity",
        "avoid",
        "usage_notes",
    )
    data: dict = {}
    for key in keys:
        value = _extract_json_like_field(raw, key)
        if value != "":
            if key == "save":
                data[key] = value.lower() in {"true", "1", "yes", "是", "保存", "收藏"}
            else:
                data[key] = value
    for key in ("tags", "emotions", "scenes", "style"):
        values = _extract_json_like_array(raw, key)
        if values:
            data[key] = values
    if data:
        return data
    return None


def _extract_json_like_field(text: str, key: str) -> str:
    quoted = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"', text, re.S)
    if quoted:
        value = quoted.group(1)
        try:
            return json.loads(f'"{value}"')
        except json.JSONDecodeError:
            return value.replace('\\"', '"').replace("\\n", "\n").strip()
    bare = re.search(rf'"{re.escape(key)}"\s*:\s*([^,\n\r}}]+)', text, re.S)
    return bare.group(1).strip().strip('"') if bare else ""


def _extract_json_like_array(text: str, key: str) -> list[str]:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\[(.*?)\]', text, re.S)
    if not match:
        return []
    raw_items = match.group(1)
    values = re.findall(r'"((?:\\.|[^"\\])*)"', raw_items)
    if not values:
        values = [part.strip().strip('"') for part in raw_items.split(",")]
    return [value.strip() for value in values if value.strip()][:8]


def _string_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()][:8]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,，、/]", value) if part.strip()][:8]
    return []
