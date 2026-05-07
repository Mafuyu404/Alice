#!/usr/bin/env python3
"""Text-only Web UI entrypoint."""

from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import time
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from kokoro import chat_session
from kokoro import character
from kokoro import config as cfg
from kokoro import llm_client
from kokoro import memory as mem_mod
from kokoro import tool_registry as tool_registry_mod
from kokoro.tool_parser import parse_sse_chunk, ToolCallAccumulator


CONFIG = cfg.load()
LLM_URL = cfg.llm_url()
MODEL = cfg.llm_model()
AVAILABLE_MODELS = CONFIG.get("available_models", [MODEL])
MEMORY_BACKEND = cfg.memory_backend()
USE_KOKOROMO = MEMORY_BACKEND == "kokoromemo" and bool(cfg.kokoromo_url())
KOKOROMO_URL = cfg.kokoromo_url() or LLM_URL

memory_backend = mem_mod.create_backend(CONFIG)
if memory_backend.ready:
    print(f"  Memory backend: {MEMORY_BACKEND}")

BASE_DIR = os.path.dirname(__file__)
KOKOROMO_DIR = CONFIG.get("kokoromo_dir", "D:/program/kokoromemo")
KOKOROMO_BIN = os.path.join(KOKOROMO_DIR, "runtime", "kokoromemo-server.exe")
LLM_BACKEND_CMD = os.environ.get("LLM_BACKEND_CMD", "").strip()
_backend_proc: Optional[subprocess.Popen] = None
_kokoromo_proc: Optional[subprocess.Popen] = None


def _start_kokoromemo() -> None:
    global _kokoromo_proc
    if not USE_KOKOROMO:
        return
    if not os.path.exists(KOKOROMO_BIN):
        print(f"  KokoroMemo binary not found: {KOKOROMO_BIN}")
        return

    env = os.environ.copy()
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    try:
        _kokoromo_proc = subprocess.Popen(
            [KOKOROMO_BIN],
            cwd=KOKOROMO_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(30):
            try:
                with socket.create_connection(("127.0.0.1", 14514), timeout=1):
                    print(f"  KokoroMemo started (PID {_kokoromo_proc.pid})")
                    return
            except Exception:
                time.sleep(0.5)
        print("  KokoroMemo startup timed out")
    except Exception as exc:
        print(f"  KokoroMemo startup failed: {exc}")
        _kokoromo_proc = None


def _stop_kokoromemo() -> None:
    global _kokoromo_proc
    if _kokoromo_proc and _kokoromo_proc.poll() is None:
        _kokoromo_proc.terminate()
        try:
            _kokoromo_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kokoromo_proc.kill()
            _kokoromo_proc.wait()
    _kokoromo_proc = None


def _start_llm_backend() -> None:
    global _backend_proc
    try:
        with httpx.Client(timeout=2) as client:
            client.get(f"{LLM_URL}/api/tags")
            print(f"  LLM backend running: {LLM_URL}")
    except Exception:
        print(f"  LLM backend unavailable: {LLM_URL}")
        if LLM_BACKEND_CMD:
            _backend_proc = subprocess.Popen(
                LLM_BACKEND_CMD,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def _stop_llm_backend() -> None:
    global _backend_proc
    if _backend_proc and _backend_proc.poll() is None:
        _backend_proc.terminate()
        try:
            _backend_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _backend_proc.kill()
            _backend_proc.wait()
    _backend_proc = None


def _stop_all() -> None:
    _stop_llm_backend()
    _stop_kokoromemo()


atexit.register(_stop_all)

app = FastAPI(title="KokoroMemo Text Chat UI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CharacterModel(BaseModel):
    name: str
    description: str = ""
    personality: str = ""
    background: str = ""
    greeting: str = ""
    example_dialogue: str = ""


class ModelSwitchRequest(BaseModel):
    model: str


@app.get("/api/characters")
def list_characters():
    return character.load()


@app.get("/api/characters/{key}")
def get_character(key: str):
    chars = character.load()
    if key not in chars:
        raise HTTPException(404, "Character not found")
    return {"key": key, **chars[key]}


@app.post("/api/characters/{key}")
def create_character(key: str, char: CharacterModel):
    chars = character.load()
    if key in chars:
        raise HTTPException(400, "Character already exists")
    chars[key] = char.model_dump()
    character.save(chars)
    return {"status": "ok"}


@app.put("/api/characters/{key}")
def update_character(key: str, char: CharacterModel):
    chars = character.load()
    if key not in chars:
        raise HTTPException(404, "Character not found")
    chars[key] = char.model_dump()
    character.save(chars)
    return {"status": "ok"}


@app.delete("/api/characters/{key}")
def delete_character(key: str):
    chars = character.load()
    if key not in chars:
        raise HTTPException(404, "Character not found")
    del chars[key]
    character.save(chars)
    return {"status": "ok"}


@app.get("/api/health")
def health():
    info = {"memory_backend": MEMORY_BACKEND}

    if MEMORY_BACKEND == "kokoromemo" and USE_KOKOROMO:
        try:
            with httpx.Client(timeout=3) as client:
                r = client.get(f"{KOKOROMO_URL}/health")
            if r.status_code == 200:
                info.update({"status": "ok", "memory": KOKOROMO_URL, "llm": LLM_URL})
                return info
        except Exception:
            pass
    elif MEMORY_BACKEND == "mem0":
        info["memory_ready"] = memory_backend.ready
    elif MEMORY_BACKEND == "none":
        info["memory"] = "none"

    try:
        with httpx.Client(timeout=3) as client:
            r = client.get(f"{LLM_URL}/api/tags")
        models = r.json().get("models", [])
        info.update({"status": "ok", "llm": LLM_URL, "models": len(models)})
    except Exception:
        info.update({"status": "error", "message": f"LLM unavailable: {LLM_URL}"})
    return info


@app.get("/api/models")
def list_models():
    return {"current": MODEL, "available": AVAILABLE_MODELS}


@app.post("/api/models/switch")
def switch_model(req: ModelSwitchRequest):
    global MODEL
    if req.model not in AVAILABLE_MODELS:
        return {
            "status": "error",
            "message": f"Model {req.model} unavailable. Choose: {', '.join(AVAILABLE_MODELS)}",
        }
    MODEL = req.model
    return {"status": "ok", "current": MODEL}


async def _kokoromemo_available() -> bool:
    if not USE_KOKOROMO:
        return False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{KOKOROMO_URL}/health")
        return r.status_code == 200
    except Exception:
        return False


@app.post("/v1/chat/completions")
async def chat_completions(request: dict):
    char_key = request.get("character_id")
    messages = request.get("messages", [])
    stream = request.get("stream", True)
    model = request.get("model", MODEL)
    user_id = char_key or "default"
    tools = request.get("tools")
    _tool_enabled = cfg.tool_enabled() and tools

    use_mem0 = not USE_KOKOROMO and memory_backend.ready
    last_input = chat_session.last_user_text(messages) if use_mem0 else ""
    if use_mem0 and last_input:
        ctx = await asyncio_get_context(last_input, user_id)
        messages = chat_session.inject_memory_context(messages, ctx)

    if _tool_enabled and tools:
        return StreamingResponse(
            _tool_aware_stream(messages, model, tools, user_id, use_mem0, last_input),
            media_type="text/event-stream",
        )

    payload = llm_client.build_payload(model, messages, stream=stream)
    headers = llm_client.api_headers(model)
    reply_chunks: list[str] = []

    async def stream_from(url: str, _payload=None, _headers=None):
        if _payload is None:
            _payload = payload
        if _headers is None:
            _headers = headers
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{url}/v1/chat/completions",
                json=_payload,
                headers=_headers,
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    err = f"[API error {resp.status_code}] {body[:200].decode(errors='replace')}"
                    data = json.dumps({"choices": [{"delta": {"content": err}}]})
                    yield f"data: {data}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    yield line + "\n"
                    content = llm_client.parse_sse_delta(line)
                    if use_mem0 and content:
                        reply_chunks.append(content)

    async def proxy_stream():
        if cfg.is_deepseek_model(model):
            async for chunk in stream_from(cfg.deepseek_url()):
                yield chunk
        elif await _kokoromemo_available():
            async for chunk in stream_from(KOKOROMO_URL):
                yield chunk
        else:
            async for chunk in stream_from(LLM_URL):
                yield chunk

        if use_mem0 and last_input and reply_chunks:
            chat_session.store_memory_async(memory_backend, last_input, "".join(reply_chunks), user_id)

    return StreamingResponse(proxy_stream(), media_type="text/event-stream")


async def _tool_aware_stream(
    messages: list[dict],
    model: str,
    tools: list[dict],
    user_id: str,
    use_mem0: bool,
    last_input: str,
):
    """Agent loop for streaming with tool calling, yielding SSE lines."""
    import asyncio
    import json as _jsonmod
    import re as _remod

    tool_names = [t["function"]["name"] for t in tools if "function" in t]
    registry = tool_registry_mod.create_registry(
        tool_list=tool_names,
        tool_timeout=cfg.tool_timeout(),
    )
    tool_schemas = registry.enabled_schemas()
    if not tool_schemas:
        # Fallback: no tools available
        payload = llm_client.build_payload(model, messages, stream=True)
        headers = llm_client.api_headers(model)
        url = _resolve_upstream_url(model)
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", f"{url}/v1/chat/completions", json=payload, headers=headers) as resp:
                async for line in resp.aiter_lines():
                    if line:
                        yield line + "\n"
        yield "data: [DONE]\n\n"
        return

    max_iter = cfg.tool_max_iterations()
    reply_chunks: list[str] = []
    working_messages = list(messages)

    for iteration in range(max_iter):
        accumulator = ToolCallAccumulator()
        pending_completed = []
        chunk_data = ""

        payload = llm_client.build_payload(model, working_messages, stream=True, tools=tool_schemas)
        headers = llm_client.api_headers(model)
        url = _resolve_upstream_url(model)

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", f"{url}/v1/chat/completions", json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    err = f"[API error {resp.status_code}] {body[:200].decode(errors='replace')}"
                    yield f"data: {_jsonmod.dumps({'choices': [{'delta': {'content': err}}]})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    parsed = parse_sse_chunk(line)

                    # Stream content to client
                    if parsed.content:
                        chunk_data += parsed.content
                        reply_chunks.append(parsed.content)
                        sse_line = f"data: {_jsonmod.dumps({'choices': [{'delta': {'content': parsed.content}}]})}\n\n"
                        yield sse_line

                    # Accumulate tool calls
                    if parsed.tool_call_deltas:
                        completed = accumulator.feed(parsed.tool_call_deltas)
                        pending_completed.extend(completed)

                    if parsed.finish_reason in ("stop", "tool_calls"):
                        break

        # If no tool calls, we're done
        if not pending_completed:
            break

        # Yield a tool call status event
        for tc in pending_completed:
            yield f"data: {_jsonmod.dumps({'type': 'tool_call', 'name': tc.name, 'status': 'running'})}\n\n"

        # Build assistant message with tool_calls
        assistant_tool_calls = []
        for tc in pending_completed:
            assistant_tool_calls.append({
                "id": tc.call_id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": _jsonmod.dumps(tc.arguments, ensure_ascii=False),
                },
            })

        working_messages.append({
            "role": "assistant",
            "content": chunk_data or None,
            "tool_calls": assistant_tool_calls,
        })

        # Execute tools and append results
        loop = asyncio.get_event_loop()
        for tc in pending_completed:
            result = await loop.run_in_executor(
                None,
                lambda n=tc.name, a=tc.arguments: registry.execute(
                    n, a,
                    memory_backend=memory_backend,
                    character_id=user_id,
                ),
            )
            working_messages.append({
                "role": "tool",
                "tool_call_id": tc.call_id,
                "content": result,
            })

        # Yield tool done event
        for tc in pending_completed:
            yield f"data: {_jsonmod.dumps({'type': 'tool_call', 'name': tc.name, 'status': 'done'})}\n\n"

    # Store to memory if applicable
    if use_mem0 and last_input and reply_chunks:
        chat_session.store_memory_async(memory_backend, last_input, "".join(reply_chunks), user_id)

    yield "data: [DONE]\n\n"


def _resolve_upstream_url(model: str) -> str:
    if cfg.is_deepseek_model(model):
        return cfg.deepseek_url()
    if USE_KOKOROMO and cfg.kokoromo_url():
        return cfg.kokoromo_url()
    return LLM_URL


async def asyncio_get_context(last_input: str, user_id: str) -> str:
    import asyncio

    return await asyncio.get_event_loop().run_in_executor(
        None,
        memory_backend.get_context,
        last_input,
        user_id,
    )


@app.get("/")
def index():
    html_path = os.path.join(BASE_DIR, "index.html")
    with open(html_path, "r", encoding="utf-8") as file:
        return HTMLResponse(file.read())


if __name__ == "__main__":
    print("=" * 50)
    print("  KokoroMemo Text Web UI")
    print("  Open: http://localhost:8080")
    print("=" * 50)

    _start_kokoromemo()
    _start_llm_backend()

    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info")
