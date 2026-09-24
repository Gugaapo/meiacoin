"""Twitch Helix helpers for Create Clip / Get Clips (MeiaCoin only)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

HELIX = "https://api.twitch.tv/helix"
USER_AGENT = "meiacoin-clips/0.1 (+https://tossemideia.cloud/meiacoin)"


def _bearer(token: str) -> str:
    t = (token or "").strip()
    if t.lower().startswith("oauth:"):
        return t.split(":", 1)[1]
    return t


class TwitchHelixError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"helix {status}: {detail}")
        self.status = status
        self.detail = detail


class TwitchHelix:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._broadcaster_id: str | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                limits=httpx.Limits(max_keepalive_connections=2, max_connections=4),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        settings = get_settings()
        return {
            "Authorization": f"Bearer {_bearer(settings.twitch_oauth_token)}",
            "Client-Id": settings.twitch_client_id,
        }

    async def refresh_oauth_token(self) -> bool:
        settings = get_settings()
        if (
            not settings.twitch_refresh_token
            or not settings.twitch_client_id
            or not settings.twitch_client_secret
        ):
            logger.warning(
                "Cannot refresh Twitch token: missing refresh_token, client_id, or client_secret"
            )
            return False
        try:
            client = await self._http()
            resp = await client.post(
                "https://id.twitch.tv/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": settings.twitch_refresh_token,
                    "client_id": settings.twitch_client_id,
                    "client_secret": settings.twitch_client_secret,
                },
            )
            if resp.status_code != 200:
                logger.error("Failed to refresh Twitch token: %s", resp.status_code)
                return False
            data = resp.json()
            access = data.get("access_token") or ""
            new_refresh = data.get("refresh_token") or settings.twitch_refresh_token
            if not access:
                logger.error("Refresh response missing access_token")
                return False
            oauth_value = access if access.startswith("oauth:") else f"oauth:{access}"
            settings.twitch_oauth_token = oauth_value
            settings.twitch_refresh_token = new_refresh
            self._persist_twitch_tokens(oauth_value, new_refresh)
            logger.info("Refreshed MeiaCoin Twitch OAuth token")
            return True
        except Exception:
            logger.exception("Error refreshing Twitch token")
            return False

    def _persist_twitch_tokens(self, oauth_token: str, refresh_token: str) -> None:
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if not env_path.exists():
            logger.warning("Cannot persist tokens: .env not found at %s", env_path)
            return
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
            updated = {"TWITCH_OAUTH_TOKEN": False, "TWITCH_REFRESH_TOKEN": False}
            out: list[str] = []
            for line in lines:
                if line.startswith("TWITCH_OAUTH_TOKEN="):
                    out.append(f"TWITCH_OAUTH_TOKEN={oauth_token}")
                    updated["TWITCH_OAUTH_TOKEN"] = True
                elif line.startswith("TWITCH_REFRESH_TOKEN="):
                    out.append(f"TWITCH_REFRESH_TOKEN={refresh_token}")
                    updated["TWITCH_REFRESH_TOKEN"] = True
                else:
                    out.append(line)
            if not updated["TWITCH_OAUTH_TOKEN"]:
                out.append(f"TWITCH_OAUTH_TOKEN={oauth_token}")
            if not updated["TWITCH_REFRESH_TOKEN"]:
                out.append(f"TWITCH_REFRESH_TOKEN={refresh_token}")
            env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        except Exception:
            logger.exception("Failed to persist Twitch tokens")

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> httpx.Response:
        client = await self._http()
        resp = await client.request(method, url, headers=self._headers(), params=params)
        if resp.status_code == 401 and retry_auth:
            if await self.refresh_oauth_token():
                resp = await client.request(
                    method, url, headers=self._headers(), params=params
                )
        return resp

    async def resolve_broadcaster_id(self) -> str:
        if self._broadcaster_id:
            return self._broadcaster_id
        settings = get_settings()
        login = (settings.twitch_channel or "omeiaum").lstrip("#").lower()
        resp = await self._request("GET", f"{HELIX}/users", params={"login": login})
        if resp.status_code != 200:
            raise TwitchHelixError(resp.status_code, resp.text[:300])
        data = (resp.json() or {}).get("data") or []
        if not data:
            raise TwitchHelixError(404, f"user not found: {login}")
        self._broadcaster_id = str(data[0]["id"])
        return self._broadcaster_id

    async def create_clip(
        self,
        *,
        title: str | None = None,
        duration: float = 30.0,
    ) -> str:
        """Start clip creation; returns clip id. Raises TwitchHelixError on failure."""
        broadcaster_id = await self.resolve_broadcaster_id()
        params: dict[str, Any] = {
            "broadcaster_id": broadcaster_id,
            "duration": max(5.0, min(60.0, float(duration))),
        }
        if title:
            params["title"] = title[:100]
        resp = await self._request("POST", f"{HELIX}/clips", params=params)
        if resp.status_code not in (200, 202):
            raise TwitchHelixError(resp.status_code, resp.text[:400])
        data = (resp.json() or {}).get("data") or []
        if not data or not data[0].get("id"):
            raise TwitchHelixError(resp.status_code, "create clip response missing id")
        return str(data[0]["id"])

    async def get_clip(self, clip_id: str) -> dict[str, Any] | None:
        resp = await self._request("GET", f"{HELIX}/clips", params={"id": clip_id})
        if resp.status_code != 200:
            raise TwitchHelixError(resp.status_code, resp.text[:300])
        data = (resp.json() or {}).get("data") or []
        if not data:
            return None
        return data[0]


helix = TwitchHelix()
