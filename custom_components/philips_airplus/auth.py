"""Authentication module for Philips Air+ integration."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Callable, Awaitable

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    AUTH_MODE_OAUTH,
    HTTP_USER_AGENT,
    OIDC_DEFAULT_ISSUER_BASE,
    OIDC_DEFAULT_REDIRECT_URI,
    OIDC_DEFAULT_SCOPES,
    OIDC_DEFAULT_TENANT_SEGMENT,
    TOKEN_REFRESH_BUFFER,
    USER_SELF_ENDPOINT,
    SIGNATURE_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)


class AuthenticationExpired(Exception):
    """Raised when authentication has expired and reauth is required."""

    pass


class PhilipsAirplusOAuth2Implementation:
    """Lightweight PKCE OAuth helper (manual code copy flow)."""

    def __init__(self, hass: HomeAssistant, client_id: Optional[str] = None) -> None:
        self.hass = hass
        self.client_id = client_id
        self.issuer_base = OIDC_DEFAULT_ISSUER_BASE.rstrip("/")
        self.tenant_segment = OIDC_DEFAULT_TENANT_SEGMENT
        self.redirect_uri = OIDC_DEFAULT_REDIRECT_URI
        self.scopes = OIDC_DEFAULT_SCOPES
        # Derived endpoints
        self.authorize_url = f"{self.issuer_base}/{self.tenant_segment}/authorize"
        self.token_url = f"{self.issuer_base}/{self.tenant_segment}/token"

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        code_verifier = secrets.token_urlsafe(32)
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )

        self.hass.data.setdefault("philips_airplus", {})[f"flow_{flow_id}"] = {
            "code_verifier": code_verifier
        }

        # Add nonce; allow override for tests
        nonce = secrets.token_urlsafe(16)
        params = {
            "client_id": self.client_id,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
            "response_mode": "query",
            "redirect_uri": self.redirect_uri,
            "ui_locales": "en-US",
            "state": flow_id,
            "nonce": nonce,
            "scope": self.scopes,
        }
        # Manual urlencode with safe space handling (%20) to mirror script
        qs = "&".join(
            f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items()
        )
        return f"{self.authorize_url}?{qs}"

    async def async_request_token(self, code: str, flow_id: str) -> dict:
        flow_data = self.hass.data.get("philips_airplus", {}).get(f"flow_{flow_id}", {})
        code_verifier = flow_data.get("code_verifier")
        if not code_verifier:
            raise RuntimeError("Code verifier not found for flow")
        # Sanitize user input.
        # Accepted formats:
        # - raw code: st2.xxxxx.sc3
        # - full redirect URL: com.philips.air://loginredirect?code=...&state=...
        # - query-only fragments containing code=...
        raw = code.strip().strip('"').strip("'")

        match = re.search(r"(?:^|[?&])code=([^&\s]+)", raw)
        if match:
            raw = urllib.parse.unquote(match.group(1))
        else:
            # Fall back to handling plain "code=..." or "...&state=..." fragments.
            if raw.startswith("code="):
                raw = raw.split("=", 1)[1]
            if "&" in raw:
                raw = raw.split("&", 1)[0]

        code = raw.strip()
        if not code:
            raise RuntimeError("Authorization code is empty after parsing")

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": code_verifier,
        }

        session = async_get_clientsession(self.hass)
        async with session.post(
            self.token_url,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": HTTP_USER_AGENT,
                "Accept": "application/json",
            },
        ) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(f"Token request failed: {response.status} - {text}")
            j = await response.json()
            return j

    async def async_refresh_token(self, refresh_token: str) -> dict:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }

        session = async_get_clientsession(self.hass)
        async with session.post(
            self.token_url,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": HTTP_USER_AGENT,
                "Accept": "application/json",
            },
        ) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(
                    f"Refresh token request failed: {response.status} - {text}"
                )
            return await response.json()


class PhilipsAirplusAuth:
    """Authentication manager for Philips Air+."""

    def __init__(
        self,
        hass: HomeAssistant,
        auth_mode: str,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        client_id: Optional[str] = None,
        token_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> None:
        self.hass = hass
        self.auth_mode = auth_mode
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at: Optional[datetime] = None
        self.user_id: Optional[str] = None
        self.signature: Optional[str] = None
        self._client_id = client_id
        self._token_callback = token_callback

    async def initialize(self) -> bool:
        """Initialize authentication and fetch user details."""
        if not self.access_token:
            return False

        try:
            # Fetch user ID
            self.user_id = await self._fetch_user_id()

            # Fetch signature
            self.signature = await self._fetch_signature()

            return True
        except Exception as ex:
            _LOGGER.error("Auth initialization failed: %s", ex)
            return False

    async def ensure_access_token(self) -> bool:
        """Ensure we have a valid access token, refreshing if necessary."""
        if not self.refresh_token:
            return bool(self.access_token)

        # If we don't know when it expires, force a refresh to establish a baseline
        if not self.expires_at:
            _LOGGER.debug("Token expiration unknown, forcing refresh")
            return await self.refresh_access_token()

        # Check if token is about to expire
        if datetime.now() + TOKEN_REFRESH_BUFFER > self.expires_at:
            _LOGGER.info("Token is about to expire, refreshing")
            return await self.refresh_access_token()

        return True

    async def refresh_access_token(self) -> bool:
        """Force refresh of access token."""
        if not self.refresh_token or not self._client_id:
            _LOGGER.error("Cannot refresh token: missing refresh_token or client_id")
            return False

        try:
            impl = PhilipsAirplusOAuth2Implementation(
                self.hass, client_id=self._client_id
            )
            token_data = await impl.async_refresh_token(self.refresh_token)

            self.access_token = token_data.get("access_token") or token_data.get(
                "accessToken"
            )
            new_refresh = token_data.get("refresh_token") or token_data.get(
                "refreshToken"
            )
            if new_refresh:
                self.refresh_token = new_refresh

            # Check for 'exp' (timestamp) first, then 'expires_in' (duration)
            exp = token_data.get("exp")
            expires_in = token_data.get("expires_in")

            if exp:
                self.expires_at = datetime.fromtimestamp(int(exp))
                _LOGGER.debug(
                    "Token refreshed, expires at (from exp): %s", self.expires_at
                )
            elif expires_in:
                self.expires_at = datetime.now() + timedelta(seconds=int(expires_in))
                _LOGGER.debug(
                    "Token refreshed, expires at (from expires_in): %s", self.expires_at
                )

            # After refreshing token, fetch new signature
            try:
                self.signature = await self._fetch_signature()
                _LOGGER.debug("Signature refreshed after token refresh")
            except Exception as sig_ex:
                _LOGGER.warning(
                    "Failed to refresh signature after token refresh: %s", sig_ex
                )
                # Continue anyway - signature refresh is not critical for token refresh success

            _LOGGER.info("Successfully refreshed access token")

            # Notify callback if registered
            if self._token_callback:
                try:
                    await self._token_callback(
                        {
                            "access_token": self.access_token,
                            "refresh_token": self.refresh_token,
                            "expires_at": self.expires_at.timestamp()
                            if self.expires_at
                            else None,
                            "client_id": self._client_id,
                        }
                    )
                except Exception as cb_ex:
                    _LOGGER.error("Failed to execute token callback: %s", cb_ex)

            return True
        except RuntimeError as ex:
            error_msg = str(ex)
            # Check if refresh token is revoked/expired (400 with invalid_grant or 401)
            if (
                "400" in error_msg and "invalid_grant" in error_msg
            ) or "401" in error_msg:
                _LOGGER.error(
                    "Refresh token has expired or been revoked. Triggering re-authentication."
                )
                # Clear refresh token to prevent further attempts
                self.refresh_token = None
                # Raise exception to trigger HA's reauth flow
                raise AuthenticationExpired(
                    "Token refresh failed - reauthentication required"
                ) from ex
            else:
                _LOGGER.error("Failed to refresh token: %s", ex)
            return False
        except Exception as ex:
            _LOGGER.error("Failed to refresh token: %s", ex)
            return False

    async def _fetch_user_id(self) -> str:
        """Fetch user ID from API."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": HTTP_USER_AGENT,
            "Accept": "application/json",
        }
        session = async_get_clientsession(self.hass)

        async with session.get(USER_SELF_ENDPOINT, headers=headers) as response:
            if response.status != 200:
                raise RuntimeError(f"Failed to fetch user ID: {response.status}")
            data = await response.json()
            return data.get("id")

    async def _fetch_signature(self) -> str:
        """Fetch signature from API."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": HTTP_USER_AGENT,
            "Accept": "application/json",
        }
        session = async_get_clientsession(self.hass)

        async with session.get(SIGNATURE_ENDPOINT, headers=headers) as response:
            if response.status != 200:
                raise RuntimeError(f"Failed to fetch signature: {response.status}")
            data = await response.json()
            return data.get("signature")

    async def close(self) -> None:
        """Close resources."""
        pass
