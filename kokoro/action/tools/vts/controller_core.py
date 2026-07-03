"""Core VTube Studio connection and parameter injection."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import pyvts

from kokoro.action.tools.vts.controller_params import (
    DEFAULT_EMOTION_KEYWORDS,
    DEFAULT_EXPRESSIONS,
    DEFAULT_LIPSYNC_CONFIG,
    DEVELOPER,
    PARAM_EYE_OPEN_L,
    PARAM_EYE_OPEN_R,
    PLUGIN_NAME,
    TOKEN_PATH,
    _ALL_VALID_PARAMS,
)

logger = logging.getLogger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _load_vts_mapping(character_id: str) -> dict | None:
    path = _PROJECT_ROOT / "characters" / character_id / "vts_mapping.json"
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("failed to load vts_mapping for %s: %s", character_id, exc)
        return None


class VTSController:
    """Manages connection, authentication, and parameter injection for VTS."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8001,
        plugin_name: str = PLUGIN_NAME,
        developer: str = DEVELOPER,
        token_path: str = TOKEN_PATH,
        character_id: str = "",
    ):
        self.character_id = character_id
        self.host = host
        self.port = port

        plugin_info = {
            "plugin_name": plugin_name,
            "developer": developer,
            "authentication_token_path": token_path,
        }
        vts_api_info = {
            "host": host, "port": port,
            "version": "1.0", "name": "VTubeStudioPublicAPI",
        }
        self.vts = pyvts.vts(plugin_info=plugin_info, vts_api_info=vts_api_info)
        self.myvts = pyvts.vts_request.VTSRequest(
            developer=developer, plugin_name=plugin_name,
        )
        self._connected = False
        self._authenticated = False
        self._available_parameter_names: set[str] = set()

        # Load expression data
        self._init_expressions(character_id)

    def _init_expressions(self, character_id: str) -> None:
        mapping = _load_vts_mapping(character_id) if character_id else None
        if mapping:
            raw_expr = mapping.get("expressions", {})
            self.expressions: dict[str, dict[str, float]] = {}
            for expr_id, params in raw_expr.items():
                self.expressions[expr_id] = {
                    k: float(v) for k, v in params.items() if k in _ALL_VALID_PARAMS
                }
            # Fill missing defaults
            for name, default_params in DEFAULT_EXPRESSIONS.items():
                self.expressions.setdefault(name, dict(default_params))
            self.emotion_keywords = mapping.get("emotion_keywords", DEFAULT_EMOTION_KEYWORDS)
            self.lipsync_config = {**DEFAULT_LIPSYNC_CONFIG, **(mapping.get("lipsync", {}))}
        else:
            self.expressions = {k: dict(v) for k, v in DEFAULT_EXPRESSIONS.items()}
            self.emotion_keywords = dict(DEFAULT_EMOTION_KEYWORDS)
            self.lipsync_config = dict(DEFAULT_LIPSYNC_CONFIG)

    # ── Connection ──────────────────────────────────────────────────────────

    async def connect(self) -> None:
        if self._connected:
            return
        await self.vts.connect()
        self._connected = True
        logger.info("VTS connected %s:%s", self.host, self.port)

    async def authenticate(self) -> None:
        if self._authenticated:
            return
        await self.connect()
        await self.vts.request_authenticate_token()
        ok = await self.vts.request_authenticate()
        if not ok:
            raise RuntimeError("VTS auth failed — accept plugin in VTS")
        self._authenticated = True
        logger.info("VTS authenticated")

    # ── Injection ───────────────────────────────────────────────────────────

    async def inject(
        self,
        params: dict[str, float],
        face_found: bool = True,
        mode: str = "set",
        weight: float = 1.0,
    ) -> dict[str, Any]:
        if not params:
            return {}
        params = self.filter_available_params(params)
        if not params:
            return {}
        await self.authenticate()
        req = self.myvts.requestSetMultiParameterValue(
            parameters=list(params.keys()),
            values=list(params.values()),
            face_found=face_found,
            mode=mode,
            weight=weight,
        )
        try:
            return await self.vts.request(req)
        except Exception as exc:
            logger.debug("VTS inject failed: %s", exc)
            return {}

    async def set_parameter(self, name: str, value: float) -> dict[str, Any]:
        return await self.inject({name: value})

    async def get_parameter(self, name: str) -> float | None:
        await self.authenticate()
        req = self.myvts.requestParameterValue(parameter=name)
        resp = await self.vts.request(req)
        data = resp.get("data", {})
        if "parameterValues" in data:
            return data["parameterValues"].get(name)
        return None

    async def get_tracking_parameters(self) -> list[dict[str, Any]]:
        await self.authenticate()
        req = self.myvts.requestTrackingParameterList()
        resp = await self.vts.request(req)
        params = (
            resp.get("data", {}).get("defaultParameters", [])
            + resp.get("data", {}).get("customParameters", [])
        )
        self._available_parameter_names = {
            str(p.get("name") or p.get("parameterName") or p.get("id") or p.get("parameterID"))
            for p in params
            if p.get("name") or p.get("parameterName") or p.get("id") or p.get("parameterID")
        }
        return params

    async def refresh_parameter_cache(self) -> set[str]:
        await self.get_tracking_parameters()
        return set(self._available_parameter_names)

    def filter_available_params(self, params: dict[str, float]) -> dict[str, float]:
        if not self._available_parameter_names:
            return dict(params)
        return {k: v for k, v in params.items() if k in self._available_parameter_names}

    # ── Expression helpers ──────────────────────────────────────────────────

    def has_expression(self, expr_id: str) -> bool:
        return expr_id in self.expressions

    def get_expression_params(self, expr_id: str, intensity: float = 1.0) -> dict[str, float]:
        base = self.expressions.get(expr_id, self.expressions.get("neutral", {}))
        if intensity >= 1.0:
            return dict(base)
        neutral = self.expressions.get("neutral", {})
        return {
            k: neutral.get(k, 0.0) + (v - neutral.get(k, 0.0)) * intensity
            for k, v in base.items()
        }

    def resolve_emotion_tone(self, tone: str) -> str | None:
        if not tone:
            return None
        if tone in self.expressions:
            return tone
        for keyword, expr_id in self.emotion_keywords.items():
            if keyword in tone:
                return expr_id
        return None

    async def set_expression(self, expr_id: str, intensity: float = 1.0) -> None:
        params = self.get_expression_params(expr_id, intensity)
        if params:
            await self.inject(params)

    async def blink(self, speed: float = 0.12) -> None:
        await self.inject({PARAM_EYE_OPEN_L: 0.0, PARAM_EYE_OPEN_R: 0.0})
        await asyncio.sleep(speed)
        await self.inject({PARAM_EYE_OPEN_L: 1.0, PARAM_EYE_OPEN_R: 1.0})

    async def close(self) -> None:
        if self._connected:
            await self.vts.close()
            self._connected = False
            self._authenticated = False
            logger.info("VTS disconnected")
