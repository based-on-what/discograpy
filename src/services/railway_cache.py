"""
railway_cache.py
================
Cache handler que extiende MemoryCacheHandler de spotipy.

Si Spotify rota el refresh token durante un access-token refresh automático,
este handler detecta el cambio y actualiza SPOTIPY_REFRESH_TOKEN en Railway
vía su GraphQL API — sin intervención manual.

Variables de Railway necesarias (las primeras tres se inyectan solas):
  RAILWAY_PROJECT_ID      → inyectada automáticamente por Railway
  RAILWAY_ENVIRONMENT_ID  → inyectada automáticamente por Railway
  RAILWAY_SERVICE_ID      → inyectada automáticamente por Railway
  RAILWAY_API_TOKEN       → créala tú en railway.com → Account → Tokens
"""

import logging
import os
from typing import Optional

import requests
from spotipy.cache_handler import MemoryCacheHandler

logger = logging.getLogger(__name__)

_RAILWAY_API = "https://backboard.railway.app/graphql/v2"

_UPSERT_MUTATION = """
mutation variableUpsert($input: VariableUpsertInput!) {
    variableUpsert(input: $input)
}
"""


class RailwayAwareCacheHandler(MemoryCacheHandler):
    """
    MemoryCacheHandler que actualiza SPOTIPY_REFRESH_TOKEN en Railway
    si Spotify rota el refresh token durante un refresh automático.

    Si RAILWAY_API_TOKEN no está configurado, se comporta exactamente
    igual que MemoryCacheHandler (sin efectos secundarios).
    """

    def __init__(self, token_info: Optional[dict] = None) -> None:
        super().__init__(token_info=token_info)
        self._known_refresh_token: Optional[str] = (token_info or {}).get("refresh_token")

    def save_token_to_cache(self, token_info: dict) -> None:
        super().save_token_to_cache(token_info)

        new_rt = token_info.get("refresh_token")
        if not new_rt or new_rt == self._known_refresh_token:
            return  # Sin cambio — nada que hacer

        logger.info("Spotify rotó el refresh token. Intentando actualizar Railway…")
        if _update_railway_var("SPOTIPY_REFRESH_TOKEN", new_rt):
            self._known_refresh_token = new_rt
            logger.info("SPOTIPY_REFRESH_TOKEN actualizado en Railway correctamente.")
        else:
            logger.warning(
                "No se pudo actualizar SPOTIPY_REFRESH_TOKEN en Railway automáticamente. "
                "Nuevo refresh token: %s — actualízalo manualmente en las variables del servicio.",
                new_rt,
            )


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

def _update_railway_var(name: str, value: str) -> bool:
    """Llama a la API GraphQL de Railway para hacer upsert de una variable."""
    api_token = os.getenv("RAILWAY_API_TOKEN")
    project_id = os.getenv("RAILWAY_PROJECT_ID")
    environment_id = os.getenv("RAILWAY_ENVIRONMENT_ID")
    service_id = os.getenv("RAILWAY_SERVICE_ID")

    missing = [
        k for k, v in {
            "RAILWAY_API_TOKEN": api_token,
            "RAILWAY_PROJECT_ID": project_id,
            "RAILWAY_ENVIRONMENT_ID": environment_id,
            "RAILWAY_SERVICE_ID": service_id,
        }.items()
        if not v
    ]
    if missing:
        logger.debug(
            "Auto-update de Railway desactivado (faltan variables: %s).",
            ", ".join(missing),
        )
        return False

    try:
        resp = requests.post(
            _RAILWAY_API,
            json={
                "query": _UPSERT_MUTATION,
                "variables": {
                    "input": {
                        "projectId": project_id,
                        "environmentId": environment_id,
                        "serviceId": service_id,
                        "name": name,
                        "value": value,
                    }
                },
            },
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            logger.error("Railway API devolvió errores: %s", data["errors"])
            return False
        return bool(data.get("data", {}).get("variableUpsert"))
    except requests.RequestException as exc:
        logger.exception("Error de red al llamar a la API de Railway: %s", exc)
        return False
    except Exception as exc:
        logger.exception("Error inesperado al actualizar variable en Railway: %s", exc)
        return False
