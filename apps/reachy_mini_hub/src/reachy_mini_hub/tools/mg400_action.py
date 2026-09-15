"""Tool que dispara acciones predefinidas en el brazo Dobot MG400 via el puente HTTP local."""

import logging
import os
from typing import Any, Dict

import httpx

from reachy_mini_hub.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

# Puente que traduce peticiones HTTP a comandos del protocolo del MG400.
# Configurar localmente la URL del PC conectado al brazo; no publicar su dirección.
MG400_BRIDGE_URL = os.getenv("MG400_BRIDGE_URL", "").strip()
MG400_ACTION_TIMEOUT_S = float(os.getenv("MG400_ACTION_TIMEOUT_S", "120"))


class Mg400Action(Tool):
    """Ejecuta una accion predefinida en el brazo robot Dobot MG400."""

    name = "mg400_action"
    description = (
        "Execute a predefined physical action on the Dobot MG400 robot arm connected to the local network "
        "(open/close the door, wave, move an object). Call this tool whenever the user asks for a physical "
        "action with the arm. Available actions: consultar_pose (read the arm position), home (return to "
        "the safe base pose), saludar (wave), abrir_puerta (open the door), cerrar_puerta (close the door). "
        "Report the result to the user in one short sentence."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["consultar_pose", "home", "saludar", "abrir_puerta", "cerrar_puerta"],
                "description": "Name of the predefined arm action to execute.",
            },
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Disparar la accion solicitada en el MG400 via el puente."""
        action = kwargs.get("action")
        if not MG400_BRIDGE_URL:
            return {"error": "Configura MG400_BRIDGE_URL localmente antes de usar el brazo."}
        if not isinstance(action, str) or not action.strip():
            return {"error": "action must be a non-empty string"}
        action = action.strip()
        logger.info("Tool call: mg400_action action=%s", action)

        try:
            async with httpx.AsyncClient(timeout=MG400_ACTION_TIMEOUT_S) as client:
                response = await client.post(f"{MG400_BRIDGE_URL}/action", json={"action": action})
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.error("mg400_action fallo: %s", exc)
            return {"error": f"El brazo MG400 no esta disponible (puente en {MG400_BRIDGE_URL}): {exc}"}

        if not isinstance(payload, dict):
            return {"error": "Respuesta invalida del puente del brazo MG400."}
        if payload.get("status") != "ok":
            return {"error": payload.get("error", "El brazo rechazo la accion.")}
        return payload
