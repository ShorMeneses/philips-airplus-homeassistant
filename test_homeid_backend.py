"""Diagnostic script for Philips Air+ / HomeID GitHub issue #28.

Tests all regional IoT API endpoints and HomeID backend discovery+profile
with your account.  Share the FULL output when reporting the issue.

Requirements: Python 3.8+, aiohttp (pip install aiohttp)

Usage (recommended — email + OTP):
  python test_homeid_backend.py

Usage (if you already have an access token from HA):
  python test_homeid_backend.py --token "eyJ..."
"""

import asyncio
import argparse
import hashlib
import json
import logging
import os
import re
import secrets
import sys
import time
import urllib.parse
from base64 import urlsafe_b64encode
from datetime import datetime

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
_LOGGER = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────

GIGYA_API_KEY = "4_JGZWlP8eQHpEqkvQElolbA"
GIGYA_API_URL = "https://cdc.accounts.home.id"
GIGYA_OTP_SEND_ENDPOINT = f"{GIGYA_API_URL}/accounts.auth.otp.email.sendCode"
GIGYA_OTP_LOGIN_ENDPOINT = f"{GIGYA_API_URL}/accounts.auth.otp.email.login"
GIGYA_SOCIALIZE_GET_IDS = f"{GIGYA_API_URL}/socialize.getIDs"

DEFAULT_CLIENT_ID = "-XsK7O6iEkLml77yDGDUi0ku"
OAUTH_CLIENT_ID_HOMEID = "-u6aTznrxp9_9e_0a57CpvEG"
OIDC_DEFAULT_REDIRECT_URI = "com.philips.air://loginredirect"
MOBILE_APP_REDIRECT_URI_HOMEID = "com.philips.ka.oneka.app.prod://oauthredirect"
OIDC_ISSUER = "https://cdc.accounts.home.id/oidc/op/v1.0/4_JGZWlP8eQHpEqkvQElolbA"
OIDC_AUTHORIZE = f"{OIDC_ISSUER}/authorize"
OIDC_TOKEN = f"{OIDC_ISSUER}/token"

OIDC_DEFAULT_SCOPES = (
    "openid email profile address DI.Account.read DI.Account.write DI.AccountProfile.read "
    "DI.AccountProfile.write DI.AccountGeneralConsent.read DI.AccountGeneralConsent.write "
    "DI.GeneralConsent.read subscriptions profile_extended consents DI.AccountSubscription.read "
    "DI.AccountSubscription.write"
)
OAUTH_SCOPES_HOMEID = (
    "openid profile email offline_access "
    "DI.Account.read DI.AccountProfile.read DI.AccountProfile.write "
    "DI.AccountGeneralConsent.read DI.AccountGeneralConsent.write "
    "DI.GeneralConsent.read DI.GeneralConsent.write "
    "VoiceProvider.read VoiceProvider.write "
    "subscriptions consent profile_extended "
    "DI.AccountSubscription.write DI.AccountSubscription.read"
)

HTTP_USER_AGENT = "okhttp/4.12.0 (Android 14; Pixel 7)"
HOMEID_USER_AGENT = "HomeID/8.16.0 (com.philips.ka.oneka.app; build:8160001; Android 14)"
HOMEID_X_USER_AGENT = "Android 14;8.16.0"
HOMEID_ACCEPT = "application/vnd.oneka.v2.0+json"

IOT_API_HOSTS = [
    "prod.eu-da.iot.versuni.com",
    "prod.us-da.iot.versuni.com",
    "prod.ap-da.iot.versuni.com",
]

HOMEID_BACKENDS = {
    "backend.vbs.versuni.com": "https://www.backend.vbs.versuni.com",
    "api.air.philips.com": "https://www.api.air.philips.com",
}

RESULTS: list[str] = []


def log_result(msg: str, *args) -> None:
    text = msg % args if args else msg
    RESULTS.append(text)
    _LOGGER.info(text)


# ── Auth helpers ───────────────────────────────────────────────────────

async def request_otp(session, email):
    params = {"email": email, "apiKey": GIGYA_API_KEY, "format": "json"}
    async with session.post(GIGYA_OTP_SEND_ENDPOINT, data=params) as resp:
        data = await resp.json(content_type=None)
    if data.get("errorCode", -1) != 0:
        raise Exception(f"OTP send failed: {data.get('errorMessage')} (code {data.get('errorCode')})")
    return data["vToken"]


async def verify_otp(session, email, code, vtoken):
    params = {"email": email, "code": code, "vToken": vtoken, "apiKey": GIGYA_API_KEY, "format": "json"}
    async with session.post(GIGYA_OTP_LOGIN_ENDPOINT, data=params) as resp:
        data = await resp.json(content_type=None)
    if data.get("errorCode", -1) != 0:
        msg = data.get("errorMessage", "unknown")
        c = data.get("errorCode")
        if c == 206001:
            raise Exception("Account pending registration — sign in once in the Philips HomeID app first")
        raise Exception(f"OTP verify failed: {msg} (code {c})")
    return data["sessionInfo"]["cookieValue"]


async def http_oauth(session, session_token, code_challenge, client_id, redirect_uri, scopes):
    auth_params = {
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
        "scope": scopes, "state": secrets.token_urlsafe(16),
        "code_challenge": code_challenge, "code_challenge_method": "S256", "prompt": "none",
    }
    async with session.get(f"{OIDC_AUTHORIZE}?{urllib.parse.urlencode(auth_params)}", allow_redirects=False) as resp:
        if resp.status not in (301, 302, 303, 307, 308):
            body = (await resp.text())[:300]
            raise Exception(f"/authorize: expected redirect, got HTTP {resp.status}: {body}")
        location = resp.headers.get("Location", "")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    context_jwt = (query.get("context") or [""])[0]
    if not context_jwt:
        raise Exception(f"/authorize: no 'context' in redirect")

    async with session.post(GIGYA_SOCIALIZE_GET_IDS, data={
        "APIKey": GIGYA_API_KEY, "includeTicket": "true", "format": "json",
    }) as resp:
        ids_data = await resp.json(content_type=None)
    gmid_ticket = ids_data.get("gmidTicket")
    if not gmid_ticket:
        raise Exception(f"socialize.getIDs: no gmidTicket ({ids_data.get('errorMessage', 'unknown')})")

    cont_params = {"context": context_jwt, "login_token": session_token, "gmidTicket": gmid_ticket, "client_id": client_id}
    async with session.get(f"{OIDC_ISSUER}/authorize/continue?{urllib.parse.urlencode(cont_params)}", allow_redirects=False) as resp:
        if resp.status not in (301, 302, 303, 307, 308):
            body = (await resp.text())[:300]
            raise Exception(f"/authorize/continue: expected redirect, got HTTP {resp.status}: {body}")
        location = resp.headers.get("Location", "")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    if query.get("errorMessage"):
        raise Exception(f"/authorize/continue: {query['errorMessage'][0]}")
    auth_code = (query.get("code") or [""])[0]
    if not auth_code:
        raise Exception(f"/authorize/continue: no 'code' in redirect")
    return auth_code


async def exchange_code(session, code, code_verifier, client_id, redirect_uri):
    data = {"client_id": client_id, "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": code_verifier}
    async with session.post(OIDC_TOKEN, data=data) as resp:
        text = await resp.text()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        raise Exception(f"Token exchange response not JSON: {text[:200]}")
    if "access_token" not in result:
        raise Exception(f"Token exchange failed: {result.get('error_description', result.get('error', 'unknown'))}")
    return result


# ── Tests ──────────────────────────────────────────────────────────────

async def test_iot_region(session, access_token, host, label):
    """Test device listing on one regional IoT host."""
    endpoint = f"https://{host}/api/da/user/self/device"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "User-Agent": HTTP_USER_AGENT}
    try:
        async with session.get(endpoint, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            body = await resp.text()
            _LOGGER.info("    HTTP %s", resp.status)
            if resp.status == 200:
                data = json.loads(body)
                if isinstance(data, dict):
                    devices = data.get("devices", [])
                elif isinstance(data, list):
                    devices = data
                else:
                    devices = []
                for d in devices:
                    _LOGGER.info("      - %s (id=%s, ctn=%s)",
                                 d.get("friendlyName") or d.get("name"),
                                 d.get("id") or d.get("uuid"),
                                 d.get("ctn") or d.get("type"))
                return devices
            else:
                _LOGGER.info("    Body: %s", body[:500])
                return None
    except Exception as e:
        _LOGGER.info("    Error: %s", e)
        return None


async def test_all_regions(session, access_token, label):
    """Test all regional IoT endpoints with the given token."""
    log_result(f"\n  [{label}] IoT API regional endpoints:")
    found_any = False
    for host in IOT_API_HOSTS:
        _LOGGER.info(f"    Host: {host}")
        devices = await test_iot_region(session, access_token, host, label)
        if devices:
            log_result(f"    ✅ {host}: {len(devices)} device(s)")
            found_any = True
        elif devices is not None:
            log_result(f"    ❌ {host}: 0 devices (empty response)")
        else:
            log_result(f"    ❌ {host}: request failed (HTTP error or timeout)")
    return found_any


async def test_homeid_backend(session, access_token, label, base_url):
    """Test discovery + profile on a HomeID backend."""
    log_result(f"\n  [{label}] HomeID backend discovery + profile:")

    discovery_url = f"{base_url}/.well-known/tenant/oneka"
    _LOGGER.info("    Discovery: GET %s", discovery_url)
    try:
        async with session.get(discovery_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            _LOGGER.info("      HTTP %s", resp.status)
            if resp.status != 200:
                log_result(f"    ❌ Discovery failed (HTTP {resp.status})")
                return False
            discovery = await resp.json(content_type=None)
            _LOGGER.info("      Body keys: %s", list(discovery.keys()))
            log_result(f"    ✅ Discovery works (HTTP 200)")
    except Exception as e:
        log_result(f"    ❌ Discovery failed: {e}")
        return False

    profile_url = discovery.get("profileUrl")
    if not profile_url:
        log_result(f"    ❌ No 'profileUrl' in discovery (keys: {list(discovery.keys())})")
        return False

    _LOGGER.info("    Raw profileUrl: %s", profile_url)
    profile_url = re.sub(r"\{[^}]*\}", "", profile_url)
    _LOGGER.info("    Stripped templates: %s", profile_url)

    if profile_url.startswith("/"):
        resolved = f"{base_url}{profile_url}"
    else:
        resolved = profile_url
    _LOGGER.info("    Resolved URL: %s", resolved)

    ts = int(time.time() * 1000)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": HOMEID_ACCEPT,
        "Accept-Language": "en-GB",
        "User-Agent": HOMEID_USER_AGENT,
        "X-USER-AGENT": HOMEID_X_USER_AGENT,
    }
    profile_req = f"{resolved}?ts={ts}"
    _LOGGER.info("    Profile: GET %s", profile_req)
    try:
        async with session.get(profile_req, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            _LOGGER.info("      HTTP %s", resp.status)
            body = await resp.text()
            _LOGGER.info("      Body: %s", body[:500])
            if resp.status == 200:
                log_result(f"    ✅ Profile endpoint works (HTTP 200)")
                return True
            else:
                log_result(f"    ❌ Profile endpoint failed (HTTP {resp.status})")
                return False
    except Exception as e:
        log_result(f"    ❌ Profile request failed: {e}")
        return False


# ── Main ───────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Philips Air+ diagnostic tool (#28)")
    parser.add_argument("--token", help="Access token (skip OTP flow)")
    args = parser.parse_args()

    log_result("=" * 60)
    log_result("  Philips Air+ / HomeID Diagnostic Script")
    log_result("  https://github.com/ShorMeneses/philips-airplus-homeassistant/issues/28")
    log_result("  Started: %s", datetime.now().isoformat())
    log_result("=" * 60)

    async with aiohttp.ClientSession() as session:
        air_token = None
        homeid_token = None

        if args.token:
            log_result("\n📋 Using direct access token (--token)")
            air_token = args.token
            homeid_token = args.token
        else:
            # ── Email OTP flow ─────────────────────────────────────────
            log_result("\n📋 Email + OTP authentication")
            email = input("  Enter your Philips account email: ").strip()
            if not email:
                _LOGGER.error("Email required")
                return

            _LOGGER.info("  Requesting OTP for %s ...", email)
            try:
                vtoken = await request_otp(session, email)
            except Exception as e:
                log_result(f"  ❌ OTP send failed: {e}")
                return
            _LOGGER.info("  OTP sent! Check your email.\n")

            code = input("  Enter verification code: ").strip()
            if not code:
                _LOGGER.error("Code required")
                return

            try:
                session_token = await verify_otp(session, email, code, vtoken)
                log_result("  ✅ Session token obtained")
            except Exception as e:
                log_result(f"  ❌ OTP verification failed: {e}")
                return

            # Exchange for Air+ tokens
            log_result("\n  Getting Air+ tokens ...")
            try:
                cv = secrets.token_urlsafe(64)
                cc = urlsafe_b64encode(hashlib.sha256(cv.encode()).digest()).rstrip(b"=").decode()
                ac = await http_oauth(session, session_token, cc, DEFAULT_CLIENT_ID, OIDC_DEFAULT_REDIRECT_URI, OIDC_DEFAULT_SCOPES)
                tokens = await exchange_code(session, ac, cv, DEFAULT_CLIENT_ID, OIDC_DEFAULT_REDIRECT_URI)
                air_token = tokens.get("access_token")
                log_result("  ✅ Air+ access token obtained")
            except Exception as e:
                log_result(f"  ❌ Air+ token exchange failed: {e}")

            # Exchange for HomeID tokens
            log_result("\n  Getting HomeID tokens ...")
            try:
                cv2 = secrets.token_urlsafe(64)
                cc2 = urlsafe_b64encode(hashlib.sha256(cv2.encode()).digest()).rstrip(b"=").decode()
                ac2 = await http_oauth(session, session_token, cc2, OAUTH_CLIENT_ID_HOMEID, MOBILE_APP_REDIRECT_URI_HOMEID, OAUTH_SCOPES_HOMEID)
                tokens2 = await exchange_code(session, ac2, cv2, OAUTH_CLIENT_ID_HOMEID, MOBILE_APP_REDIRECT_URI_HOMEID)
                homeid_token = tokens2.get("access_token")
                log_result("  ✅ HomeID access token obtained")
            except Exception as e:
                log_result(f"  ❌ HomeID token exchange failed: {e}")

        if not air_token and not homeid_token:
            log_result("\n❌ No tokens available — cannot run tests")
            return

        # ── TEST 1: IoT API with Air+ tokens ───────────────────────────
        log_result("\n" + "━" * 55)
        log_result("📡 TEST 1: IoT API device listing — Air+ tokens")
        log_result("━" * 55)
        air_found = False
        if air_token:
            air_found = await test_all_regions(session, air_token, "Air+")

        # ── TEST 2: IoT API with HomeID tokens ─────────────────────────
        log_result("\n" + "━" * 55)
        log_result("📡 TEST 2: IoT API device listing — HomeID tokens")
        log_result("━" * 55)
        homeid_found = False
        if homeid_token:
            homeid_found = await test_all_regions(session, homeid_token, "HomeID")

        # ── TEST 3: HomeID backend discovery+profile ──────────────────
        log_result("\n" + "━" * 55)
        log_result("🏠 TEST 3: HomeID backend discovery + profile")
        log_result("━" * 55)

        chosen = homeid_token or air_token
        if chosen:
            log_result(f"  Using token type: {'HomeID' if homeid_token else 'Air+'}")
            for label, base_url in HOMEID_BACKENDS.items():
                await test_homeid_backend(session, chosen, label, base_url)
        else:
            log_result("  No token available for HomeID backend test")

        # ── Summary ───────────────────────────────
        log_result("\n" + "=" * 60)
        log_result("  SUMMARY")
        log_result("=" * 60)

        log_result(f"\n  IoT API (Air+ tokens):  {'✅ Found devices' if air_found else '❌ No devices on any region'}")
        log_result(f"  IoT API (HomeID tokens): {'✅ Found devices' if homeid_found else '❌ No devices on any region'}")

        if not air_found and not homeid_found:
            log_result("\n  ⚠️  No devices found on ANY IoT API region!")
            log_result("     This means your account type or region may differ.")
            log_result("     Check the HomeID backend results below.")

        log_result("\n  ✅ Tests complete.")
        log_result('  Share the FULL output on:')
        log_result('  https://github.com/ShorMeneses/philips-airplus-homeassistant/issues/28')

    # Save to file
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(os.path.dirname(__file__) or ".", f"philips_diagnostic_{ts}.txt")
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(RESULTS))
        _LOGGER.info("\nResults saved to: %s", out_path)
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
