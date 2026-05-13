"""VTube Studio integration via pyvts WebSocket API.

VTS tracking parameters (used for injection):
  EyeOpenLeft / EyeOpenRight    0.0(closed) - 1.0(open)
  MouthOpen                     0.0 - 1.0
  MouthSmile                    0.0 - 1.0
  Brows                         0.0 - 1.0
  FaceAngleX / Y / Z            head rotation
  FacePositionX / Y / Z         head movement
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pyvts

logger = logging.getLogger(__name__)

PLUGIN_NAME = "alice-vts"
DEVELOPER = "Alice"
TOKEN_PATH = "./vts_token.txt"
VTS_HOST = "localhost"
VTS_PORT = 8001

# Tracking parameter names (NOT Live2D internal params like EyeBlinkLeft)
PARAM_EYE_OPEN_L = "EyeOpenLeft"
PARAM_EYE_OPEN_R = "EyeOpenRight"
PARAM_MOUTH_OPEN = "MouthOpen"
PARAM_MOUTH_SMILE = "MouthSmile"
PARAM_BROWS = "Brows"
PARAM_FACE_ANGLE_X = "FaceAngleX"
PARAM_FACE_ANGLE_Y = "FaceAngleY"
PARAM_FACE_ANGLE_Z = "FaceAngleZ"


class VTSController:
    """Manages connection and parameter control for VTube Studio."""

    def __init__(
        self,
        host: str = VTS_HOST,
        port: int = VTS_PORT,
        plugin_name: str = PLUGIN_NAME,
        developer: str = DEVELOPER,
        token_path: str = TOKEN_PATH,
    ) -> None:
        plugin_info = {
            "plugin_name": plugin_name,
            "developer": developer,
            "authentication_token_path": token_path,
        }
        vts_api_info = {
            "host": host,
            "port": port,
            "version": "1.0",
            "name": "VTubeStudioPublicAPI",
        }
        self.vts = pyvts.vts(plugin_info=plugin_info, vts_api_info=vts_api_info)
        self.myvts = pyvts.vts_request.VTSRequest(
            developer=developer, plugin_name=plugin_name
        )
        self._connected = False
        self._authenticated = False

    async def connect(self) -> None:
        """Connect to VTube Studio WebSocket API."""
        if self._connected:
            return
        await self.vts.connect()
        self._connected = True
        logger.info("Connected to VTube Studio at %s:%s", VTS_HOST, VTS_PORT)

    async def authenticate(self) -> None:
        """Authenticate with VTube Studio.

        First time: VTS will prompt you to accept the plugin dialog.
        The token is saved locally for subsequent connections.
        """
        if self._authenticated:
            return
        await self.connect()

        await self.vts.request_authenticate_token()
        logger.info("VTS auth token status: %d", self.vts.get_authentic_status())

        ok = await self.vts.request_authenticate()
        if not ok:
            raise RuntimeError("VTS authentication failed — accept the plugin in VTS")
        self._authenticated = True
        logger.info("VTS authenticated")

    async def inject(
        self,
        params: dict[str, float],
        face_found: bool = True,
        mode: str = "set",
        weight: float = 1.0,
    ) -> dict[str, Any]:
        """Inject tracking parameter values into VTS.

        Args:
            params: parameter name → value dict
            face_found: tell VTS face is detected (True) so it accepts input
            mode: "set", "add", or "multiply"
            weight: blend between plugin and tracking (0-1, 1=full plugin)
        """
        await self.authenticate()
        req = self.myvts.requestSetMultiParameterValue(
            parameters=list(params.keys()),
            values=list(params.values()),
            face_found=face_found,
            mode=mode,
            weight=weight,
        )
        return await self.vts.request(req)

    async def set_parameter(self, param_name: str, value: float) -> dict[str, Any]:
        """Set a single tracking parameter value."""
        return await self.inject({param_name: value})

    async def get_parameter(self, param_name: str) -> float | None:
        """Get the current value of a tracking parameter."""
        await self.authenticate()
        req = self.myvts.requestParameterValue(parameter=param_name)
        response = await self.vts.request(req)
        data = response.get("data", {})
        if "parameterValues" in data:
            return data["parameterValues"].get(param_name)
        return None

    async def get_tracking_parameters(self) -> list[dict[str, Any]]:
        """Get list of all available tracking parameters."""
        await self.authenticate()
        req = self.myvts.requestTrackingParameterList()
        response = await self.vts.request(req)
        default = response.get("data", {}).get("defaultParameters", [])
        custom = response.get("data", {}).get("customParameters", [])
        return default + custom

    async def blink(self, speed: float = 0.12) -> None:
        """Perform one blink cycle.

        Injects EyeOpenLeft/EyeOpenRight = 0 (closed) for 'speed' seconds,
        then restores to 1 (open). Uses face_found=True so VTS accepts the input.
        """
        await self.inject(
            {PARAM_EYE_OPEN_L: 0.0, PARAM_EYE_OPEN_R: 0.0},
            face_found=True,
        )
        await asyncio.sleep(speed)
        await self.inject(
            {PARAM_EYE_OPEN_L: 1.0, PARAM_EYE_OPEN_R: 1.0},
            face_found=True,
        )

    async def close(self) -> None:
        """Disconnect from VTube Studio."""
        if self._connected:
            await self.vts.close()
            self._connected = False
            self._authenticated = False
            logger.info("Disconnected from VTube Studio")


async def run_blink_demo():
    """Simple demo: continuously blink the model.

    NOTE: If VTS face tracking (webcam/iPhone) is active, it may override
    injected values. Try disabling Tracking in VTS or covering your camera
    for best results.
    """
    ctrl = VTSController()
    try:
        await ctrl.connect()
        await ctrl.authenticate()
        logger.info("Blink demo started. Press Ctrl+C to stop.")
        count = 0
        while True:
            await ctrl.blink()
            count += 1
            logger.info("Blink #%d", count)
            await asyncio.sleep(4.0)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await ctrl.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_blink_demo())
