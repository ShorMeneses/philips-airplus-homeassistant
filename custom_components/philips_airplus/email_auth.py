"""Email + OTP authentication for Philips Air+ via Gigya CDC."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import time
import urllib.parse
from base64 import urlsafe_b64encode
from datetime import datetime, timedelta
from typing import Any
from typing import Optional

import aiohttp
from homeassistant.core import HomeAssistant

from .const import (
    API_BASE_URL,
    DEFAULT_CLIENT_ID,
    DEVICE_ENDPOINT,
    GIGYA_API_KEY,
    GIGYA_API_URL,
    GIGYA_OTP_SEND_ENDPOINT,
    GIGYA_OTP_LOGIN_ENDPOINT,
    GIGYA_SOCIALIZE_GET_IDS,
    HTTP_USER_AGENT,
    MOBILE_APP_REDIRECT_URI_HOMEID,
    OAUTH_CLIENT_ID_HOMEID,
    OIDC_DEFAULT_REDIRECT_URI,
    OIDC_DEFAULT_SCOPES,
    OIDC_DEFAULT_ISSUER_BASE,
    OIDC_DEFAULT_TENANT_SEGMENT,
    OIDC_HOMEID_ISSUER,
    OIDC_HOMEID_TOKEN,
)

_LOGGER = logging.getLogger(__name__)

# Scopes matching the Philips HomeID app (prompt=none compatible)
OAUTH_SCOPES_HOMEID = (
    "openid profile email offline_access "
    "DI.Account.read DI.AccountProfile.read DI.AccountProfile.write "
    "DI.AccountGeneralConsent.read DI.AccountGeneralConsent.write "
    "DI.GeneralConsent.read DI.GeneralConsent.write "
    "VoiceProvider.read VoiceProvider.write "
    "subscriptions consent profile_extended "
    "DI.AccountSubscription.write DI.AccountSubscription.read"
)

# HomeID backend base (for appliance discovery fallback)
HOMEID_BACKEND_BASE = "https://www.backend.vbs.versuni.com"
HOMEID_BACKEND_API = f"{HOMEID_BACKEND_BASE}/api"
HOMEID_ACCEPT = "application/vnd.oneka.v2.0+json"
HOMEID_USER_AGENT = (
    "HomeID/8.16.0 (com.philips.ka.oneka.app; build:8160001; Android 14)"
)
HOMEID_X_USER_AGENT = "Android 14;8.16.0"


class EmailOTPAuthError(Exception):
    """Error during email OTP authentication."""


class EmailOTPAuth:
    """Handles Gigya email OTP authentication and OIDC token exchange.

    Defaults to the Philips Air+ OIDC client so the resulting tokens are
    accepted by the IoT device-list API.  Stores the Gigya session token
    internally so callers who get zero devices from the IoT API can call
    ``exchange_session_for_tokens_homeid_fallback()`` to obtain HomeID
    tokens instead.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        client_id: str = DEFAULT_CLIENT_ID,
        redirect_uri: str = OIDC_DEFAULT_REDIRECT_URI,
        scopes: str = OIDC_DEFAULT_SCOPES,
        issuer_base: str = OIDC_DEFAULT_ISSUER_BASE,
        tenant_segment: str = OIDC_DEFAULT_TENANT_SEGMENT,
    ) -> None:
        self.hass = hass
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._scopes = scopes
        self._issuer_base = issuer_base.rstrip("/")
        self._tenant_segment = tenant_segment
        self._authorize_url = (
            f"{self._issuer_base}/{self._tenant_segment}/authorize"
        )
        self._token_url = f"{self._issuer_base}/{self._tenant_segment}/token"
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_token: Optional[str] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def request_otp(self, email: str) -> str:
        """Send OTP code to email. Returns vToken for verification."""
        session = await self._get_session()
        params = {
            "email": email.strip(),
            "apiKey": GIGYA_API_KEY,
            "format": "json",
        }

        _LOGGER.debug("Requesting OTP for %s", email)
        try:
            async with session.post(GIGYA_OTP_SEND_ENDPOINT, data=params) as resp:
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as ex:
            raise EmailOTPAuthError(f"OTP endpoint unreachable: {ex}") from ex
        except json.JSONDecodeError as ex:
            raise EmailOTPAuthError(
                f"OTP endpoint returned non-JSON (HTTP {resp.status})"
            ) from ex

        error_code = data.get("errorCode", -1)
        if error_code != 0:
            msg = data.get("errorMessage", "Unknown error")
            raise EmailOTPAuthError(f"OTP send failed: {msg} (code {error_code})")

        vtoken = data.get("vToken")
        if not vtoken:
            raise EmailOTPAuthError("No vToken in OTP send response")

        _LOGGER.debug("OTP sent to %s", email)
        return vtoken

    async def verify_otp(self, email: str, code: str, vtoken: str) -> str:
        """Verify OTP code. Returns Gigya session token."""
        session = await self._get_session()
        params = {
            "email": email.strip(),
            "code": code.strip(),
            "vToken": vtoken,
            "apiKey": GIGYA_API_KEY,
            "format": "json",
        }

        _LOGGER.debug("Verifying OTP for %s", email)
        try:
            async with session.post(GIGYA_OTP_LOGIN_ENDPOINT, data=params) as resp:
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as ex:
            raise EmailOTPAuthError(f"OTP verify endpoint unreachable: {ex}") from ex
        except json.JSONDecodeError as ex:
            raise EmailOTPAuthError(
                f"OTP verify returned non-JSON (HTTP {resp.status})"
            ) from ex

        error_code = data.get("errorCode", -1)
        if error_code == 206001:
            raise EmailOTPAuthError(
                "Account pending registration: this email is not a fully "
                "registered Philips account. Sign in once in the Philips "
                "HomeID app to complete registration, then try again."
            )
        if error_code != 0:
            msg = data.get("errorMessage", "Unknown error")
            raise EmailOTPAuthError(f"OTP verification failed: {msg}")

        session_token = data.get("sessionInfo", {}).get("cookieValue")
        if not session_token:
            raise EmailOTPAuthError("No session token in OTP verify response")

        _LOGGER.debug("OTP verified for %s", email)
        return session_token

    async def exchange_session_for_tokens(self, session_token: str) -> dict[str, Any]:
        """Exchange Gigya session token for OIDC access/refresh tokens via pure HTTP."""
        self._session_token = session_token
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = (
            urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )

        auth_code = await self._http_oauth(session_token, code_challenge)
        return await self._exchange_code(auth_code, code_verifier)

    async def exchange_session_for_tokens_homeid_fallback(self) -> dict[str, Any] | None:
        """Re-use the stored session token to get HomeID OIDC tokens.

        Returns None if no session token is stored or exchange fails.
        Use when the IoT API returns no devices with the Air+ tokens.
        """
        if not self._session_token:
            return None
        try:
            code_verifier = secrets.token_urlsafe(64)
            code_challenge = (
                urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            auth_code = await self._http_oauth(
                self._session_token, code_challenge,
                client_id=OAUTH_CLIENT_ID_HOMEID,
                redirect_uri=MOBILE_APP_REDIRECT_URI_HOMEID,
                scopes=OAUTH_SCOPES_HOMEID,
            )
            return await self._exchange_code(
                auth_code, code_verifier,
                client_id=OAUTH_CLIENT_ID_HOMEID,
                redirect_uri=MOBILE_APP_REDIRECT_URI_HOMEID,
            )
        except EmailOTPAuthError as ex:
            _LOGGER.warning("HomeID fallback exchange failed: %s", ex)
            return None

    async def _http_oauth(self, session_token: str, code_challenge: str,
                          client_id: str | None = None,
                          redirect_uri: str | None = None,
                          scopes: str | None = None) -> str:
        """Pure-HTTP OAuth flow with prompt=none. Returns authorization code."""
        session = await self._get_session()
        cid = client_id or self._client_id
        ruri = redirect_uri or self._redirect_uri
        sc = scopes or self._scopes

        # Step 1: GET /authorize?prompt=none → expect redirect with context JWT
        auth_params = {
            "client_id": cid,
            "response_type": "code",
            "redirect_uri": ruri,
            "scope": sc,
            "state": secrets.token_urlsafe(16),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "none",
        }
        auth_url = f"{self._authorize_url}?{urllib.parse.urlencode(auth_params)}"

        async with session.get(auth_url, allow_redirects=False) as resp:
            if resp.status not in (301, 302, 303, 307, 308):
                body = (await resp.text())[:300]
                raise EmailOTPAuthError(
                    f"/authorize: expected redirect, got HTTP {resp.status}: {body}"
                )
            location = resp.headers.get("Location", "")

        query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        context_jwt = (query.get("context") or [""])[0]
        if not context_jwt:
            raise EmailOTPAuthError(
                f"/authorize: no 'context' in redirect ({location[:200]})"
            )

        # Step 2: POST socialize.getIDs → get gmidTicket
        async with session.post(
            GIGYA_SOCIALIZE_GET_IDS,
            data={
                "APIKey": GIGYA_API_KEY,
                "includeTicket": "true",
                "format": "json",
            },
        ) as resp:
            ids_data = await resp.json(content_type=None)
        gmid_ticket = ids_data.get("gmidTicket")
        if not gmid_ticket:
            err = ids_data.get("errorMessage") or ids_data.get("errorCode")
            raise EmailOTPAuthError(f"socialize.getIDs returned no gmidTicket: {err}")

        # Step 3: GET /authorize/continue → expect redirect with code
        issuer = f"{self._issuer_base}/{self._tenant_segment}"
        cont_params = {
            "context": context_jwt,
            "login_token": session_token,
            "gmidTicket": gmid_ticket,
            "client_id": cid,
        }
        cont_url = (
            f"{issuer}/authorize/continue?"
            f"{urllib.parse.urlencode(cont_params)}"
        )
        async with session.get(cont_url, allow_redirects=False) as resp:
            if resp.status not in (301, 302, 303, 307, 308):
                body = (await resp.text())[:300]
                raise EmailOTPAuthError(
                    f"/authorize/continue: expected redirect, got HTTP {resp.status}: {body}"
                )
            location = resp.headers.get("Location", "")

        query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        if query.get("errorMessage"):
            raise EmailOTPAuthError(
                f"/authorize/continue: {query['errorMessage'][0]}"
            )
        auth_code = (query.get("code") or [""])[0]
        if not auth_code:
            raise EmailOTPAuthError(
                f"/authorize/continue: no 'code' in redirect ({location[:200]})"
            )
        return auth_code

    async def _exchange_code(self, code: str, code_verifier: str,
                             client_id: str | None = None,
                             redirect_uri: str | None = None) -> dict[str, Any]:
        """Exchange authorization code for OIDC tokens."""
        session = await self._get_session()
        cid = client_id or self._client_id
        ruri = redirect_uri or self._redirect_uri
        data = {
            "client_id": cid,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": ruri,
            "code_verifier": code_verifier,
        }

        _LOGGER.debug("Exchanging auth code for tokens at %s", self._token_url)
        async with session.post(self._token_url, data=data) as resp:
            text = await resp.text()
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                raise EmailOTPAuthError(
                    f"Token exchange response not JSON: {text[:200]}"
                )

        if "access_token" not in result:
            error = result.get("error_description", result.get("error", "Unknown"))
            raise EmailOTPAuthError(f"Token exchange failed: {error}")

        _LOGGER.debug(
            "OIDC tokens obtained (scopes: %s, expires_in: %s)",
            result.get("scope", "?"),
            result.get("expires_in", "?"),
        )

        normalized: dict[str, Any] = {
            "access_token": result.get("access_token"),
            "refresh_token": result.get("refresh_token"),
        }

        exp = result.get("exp")
        expires_in = result.get("expires_in")
        if exp:
            normalized["token_expires_at"] = int(exp)
        elif expires_in:
            normalized["token_expires_at"] = int(
                (datetime.now() + timedelta(seconds=int(expires_in))).timestamp()
            )

        return normalized

    # ------------------------------------------------------------------
    # HomeID backend API fallback – used when the IoT API returns no
    # devices for tokens obtained via the HomeID client.
    # ------------------------------------------------------------------

    async def list_devices_via_homeid(self, access_token: str) -> list[dict[str, Any]]:
        """Discover appliances via the HomeID backend HAL API."""
        session = await self._get_session()

        # 1. Discovery
        discovery_url = f"{HOMEID_BACKEND_BASE}/.well-known/tenant/oneka"
        _LOGGER.info("HomeID discovery: GET %s", discovery_url)
        async with session.get(discovery_url) as resp:
            if resp.status != 200:
                _LOGGER.warning("HomeID discovery failed: HTTP %s", resp.status)
                return []
            discovery = await resp.json(content_type=None)
            _LOGGER.debug("HomeID discovery body: %s", json.dumps(discovery)[:2000])

        profile_url = discovery.get("profileUrl")
        if not profile_url:
            _LOGGER.warning("HomeID discovery missing profileUrl, keys: %s", list(discovery.keys()))
            return []
        _LOGGER.info("HomeID discovery profileUrl: %s", profile_url)
        profile_url = re.sub(r"\{[^}]*\}", "", profile_url)
        if profile_url.startswith("/"):
            profile_url = f"{HOMEID_BACKEND_BASE}{profile_url}"

        ts = int(time.time() * 1000)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": HOMEID_ACCEPT,
            "Accept-Language": "en-GB",
            "User-Agent": HOMEID_USER_AGENT,
            "X-USER-AGENT": HOMEID_X_USER_AGENT,
        }

        # 2. Profile
        profile_req = f"{profile_url}?ts={ts}"
        _LOGGER.info("HomeID profile: GET %s", profile_req)
        async with session.get(profile_req, headers=headers) as resp:
            if resp.status != 200:
                _LOGGER.warning(
                    "HomeID profile failed: HTTP %s, body: %s",
                    resp.status,
                    (await resp.text())[:500],
                )
                return []
            profile = await resp.json(content_type=None)
            _LOGGER.debug("HomeID profile body: %s", json.dumps(profile)[:2000])

        # Check for embedded appliances
        embedded = profile.get("_embedded", {})
        appliances_embedded = embedded.get("userAppliances", {})
        if isinstance(appliances_embedded, dict):
            items = appliances_embedded.get("_embedded", {}).get("item", [])
            if items:
                _LOGGER.info("HomeID: %d embedded appliance(s) in profile", len(items))
                for it in items:
                    _LOGGER.debug("  Appliance: name=%s externalDeviceId=%s ctn=%s",
                                  it.get("name"), it.get("externalDeviceId"), it.get("ctn"))
                return self._normalize_appliances(items)

        # Follow userAppliances link
        links = profile.get("_links", {})
        _LOGGER.debug("HomeID profile _links keys: %s", list(links.keys()))
        appliances_link = links.get("userAppliances", {})
        href = (
            appliances_link.get("href", "")
            if isinstance(appliances_link, dict)
            else ""
        )
        if not href:
            # Try alternative link names
            for alt in ("customerAppliances", "appliances", "devices"):
                alt_link = links.get(alt, {})
                href = alt_link.get("href", "") if isinstance(alt_link, dict) else ""
                if href:
                    _LOGGER.debug("Found appliances via '%s' link", alt)
                    break
        if not href:
            _LOGGER.warning("HomeID profile has no appliance links, _links keys: %s", list(links.keys()))
            return []
        if href.startswith("/"):
            href = f"{HOMEID_BACKEND_API}{href}"
        href = re.sub(r"\{[^}]*\}", "", href)

        # 3. Appliances
        appliances_req = f"{href}?ts={ts}"
        _LOGGER.info("HomeID appliances: GET %s", appliances_req)
        async with session.get(appliances_req, headers=headers) as resp:
            if resp.status != 200:
                _LOGGER.warning(
                    "HomeID appliances failed: HTTP %s, body: %s",
                    resp.status,
                    (await resp.text())[:500],
                )
                return []
            data = await resp.json(content_type=None)
            _LOGGER.debug("HomeID appliances body: %s", json.dumps(data)[:2000])

        if isinstance(data, dict):
            items = data.get("_embedded", {}).get("item", [])
        elif isinstance(data, list):
            items = data
        else:
            items = []
        _LOGGER.info("HomeID: %d appliance(s) from API", len(items))
        for it in items:
            _LOGGER.debug("  Appliance: name=%s externalDeviceId=%s ctn=%s",
                          it.get("name"), it.get("externalDeviceId"), it.get("ctn"))
        return self._normalize_appliances(items)

    @staticmethod
    def _normalize_appliances(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert HomeID appliance dicts to IoT-compatible format."""
        out: list[dict[str, Any]] = []
        for item in items:
            name = item.get("name") or item.get("friendlyName") or "Unknown"
            mac = item.get("macAddress", "")
            ctn = item.get("ctn") or item.get("modelNumber") or item.get("deviceType") or ""
            device_id = item.get("externalDeviceId") or item.get("id") or mac
            uuid_val = item.get("uuid") or device_id
            out.append({
                "uuid": uuid_val,
                "id": device_id,
                "name": name,
                "deviceName": name,
                "friendlyName": name,
                "ctn": ctn,
                "type": ctn or "unknown",
                "macAddress": mac,
            })
        return out
