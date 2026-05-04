#!/usr/bin/env python3
"""Optional local OpenAI-compatible LLM server backed by transformers."""

from __future__ import annotations

import argparse
import json
import threading
from queue import Queue

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from kokoro import config as cfg

app = FastAPI(title="Local LLM")
_model = None
_tokenizer = None
_model_name = ""
_load_lock = threading.Lock()


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[dict]
    stream: bool = True
    max_tokens: int = 512
    temperature: float = 0.7


def load_model(model_name: str):
    global _model, _tokenizer, _model_name
    with _load_lock:
        if _model is not None and _model_name == model_name:
            return _model, _tokenizer

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        _tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
        _model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float32,
            device_map="cpu",
            trust_remote_code=True,
        )
        _model.eval()
        _model_name = model_name
        return _model, _tokenizer


def generate_text(req: ChatRequest) -> str:
    model_name = req.model or cfg.get("local_model", "Qwen/Qwen2.5-1.5B-Instruct")
    model, tokenizer = load_model(model_name)
    prompt = tokenizer.apply_chat_template(req.messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=2048)
    outputs = model.generate(
        **inputs,
        max_new_tokens=req.max_tokens,
        temperature=req.temperature,
        do_sample=req.temperature > 0,
        pad_token_id=tokenizer.eos_token_id,
    )
    generated = outputs[0][inputs["input_ids"].shape[1] :]
    return tokenizer.decode(generated, skip_special_tokens=True)


@app.get("/v1/models")
def models():
    model_name = cfg.get("local_model", "Qwen/Qwen2.5-1.5B-Instruct")
    return {"object": "list", "data": [{"id": model_name, "object": "model"}]}


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest):
    text = generate_text(req)
    if not req.stream:
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ]
        }

    def stream():
        for char in text:
            payload = {"choices": [{"delta": {"content": char}}]}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local transformers LLM server")
    parser.add_argument("--model", default=cfg.get("local_model", "Qwen/Qwen2.5-1.5B-Instruct"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=14515)
    args = parser.parse_args()
    cfg.load()["local_model"] = args.model
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
