"""API client for Philips Air+ integration."""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, Dict, List, Optional

import aiohttp

from .const import (
    API_BASE_URL,
    DEVICE_ENDPOINT,
    HTTP_USER_AGENT,
    SIGNATURE_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)


class PhilipsAirplusAPIError(Exception):
    """Exception for Philips Air+ API errors."""


class PhilipsAirplusAPIClient:
    """API client for Philips Air+."""

    def __init__(self, access_token: str) -> None:
        """Initialize API client."""
        self.access_token = access_token
        self._session: Optional[aiohttp.ClientSession] = None

    def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "User-Agent": HTTP_USER_AGENT,
        }

    async def _fetch_json(self, url: str, timeout: int = 20) -> Dict[str, Any]:
        """Fetch JSON from API endpoint."""
        session = self._get_session()
        headers = self._get_headers()

        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    raise PhilipsAirplusAPIError(f"HTTP {response.status}: {text}")

                data = await response.json()
                _LOGGER.debug("API response from %s: %s", url, data)
                return data

        except aiohttp.ClientError as ex:
            raise PhilipsAirplusAPIError(f"Network error: {ex}") from ex
        except json.JSONDecodeError as ex:
            raise PhilipsAirplusAPIError(f"Invalid JSON response: {ex}") from ex

    async def list_devices(self) -> List[Dict[str, Any]]:
        """List all devices associated with the account."""
        try:
            data = await self._fetch_json(DEVICE_ENDPOINT)
            devices = []

            if isinstance(data, dict):
                if isinstance(data.get("devices"), list):
                    devices = data["devices"]
                else:
                    # Fallback: locate any list with uuid entries
                    for key, value in data.items():
                        if isinstance(value, list) and any(
                            isinstance(item, dict) and item.get("uuid")
                            for item in value
                        ):
                            devices = value
                            break
            elif isinstance(data, list):
                devices = data

            _LOGGER.debug("Found %d devices", len(devices))
            return devices

        except PhilipsAirplusAPIError:
            raise
        except Exception as ex:
            raise PhilipsAirplusAPIError(f"Failed to list devices: {ex}") from ex

    async def fetch_signature(self) -> str:
        """Fetch MQTT signature."""
        try:
            data = await self._fetch_json(SIGNATURE_ENDPOINT)
            signature = data.get("signature")

            if not signature:
                raise PhilipsAirplusAPIError("Signature missing in response")

            _LOGGER.debug("Successfully fetched MQTT signature")
            return signature

        except PhilipsAirplusAPIError:
            raise
        except Exception as ex:
            raise PhilipsAirplusAPIError(f"Failed to fetch signature: {ex}") from ex

    async def get_user_info(self) -> Dict[str, Any]:
        """Get user information."""
        try:
            user_endpoint = f"{API_BASE_URL}/da/user/self"
            data = await self._fetch_json(user_endpoint)
            return data

        except PhilipsAirplusAPIError:
            raise
        except Exception as ex:
            raise PhilipsAirplusAPIError(f"Failed to get user info: {ex}") from ex


class PhilipsAirplusDevice:
    """Representation of a Philips Air+ device."""

    def __init__(self, device_data: Dict[str, Any]) -> None:
        """Initialize device."""
        self._data = device_data
        self._uuid = self._extract_uuid()
        self._name = self._extract_name()
        self._type = self._extract_type()

    def _extract_uuid(self) -> str:
        """Extract device UUID."""
        return self._data.get("uuid") or self._data.get("id") or "unknown"

    def _extract_name(self) -> str:
        """Extract device name."""
        return (
            self._data.get("name")
            or self._data.get("deviceName")
            or self._data.get("friendlyName")
            or f"Air+ {self._uuid[:8]}"
        )

    def _extract_type(self) -> str:
        """Extract device type."""
        return self._data.get("type") or self._data.get("deviceType") or "unknown"

    @property
    def uuid(self) -> str:
        """Get device UUID."""
        return self._uuid

    @property
    def name(self) -> str:
        """Get device name."""
        return self._name

    @property
    def type(self) -> str:
        """Get device type."""
        return self._type

    @property
    def data(self) -> Dict[str, Any]:
        """Get raw device data."""
        return self._data

    def __str__(self) -> str:
        """String representation."""
        return f"{self.name} ({self.uuid})"

    def __repr__(self) -> str:
        """Representation."""
        return f"PhilipsAirplusDevice(uuid={self.uuid!r}, name={self.name!r}, type={self.type!r})"


def extract_user_id_from_token(token: str) -> Optional[str]:
    """Extract user ID from JWT token."""
    try:
        import base64

        parts = token.split(".")
        if len(parts) < 2:
            return None

        # Decode the payload (middle part)
        payload = parts[1]
        # Add padding if needed
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding)
        payload_data = json.loads(decoded)

        return payload_data.get("sub")
    except Exception as ex:
        _LOGGER.debug("Failed to extract user ID from token: %s", ex)
        return None


def extract_expiration_from_token(token: str) -> Optional[int]:
    """Extract expiration timestamp from JWT token."""
    try:
        import base64

        parts = token.split(".")
        if len(parts) < 2:
            return None

        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding)
        payload_data = json.loads(decoded)

        return payload_data.get("exp")
    except Exception as ex:
        _LOGGER.debug("Failed to extract expiration from token: %s", ex)
        return None


def build_client_id(user_id: str, device_uuid: str) -> str:
    """Build composite client ID for MQTT connection."""
    import re

    # Remove da- prefix if present
    if device_uuid.startswith("da-"):
        device_uuid = device_uuid[3:]

    user_id = user_id.strip()

    # UUID regex pattern
    uuid_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
    )

    if uuid_re.match(user_id) and uuid_re.match(device_uuid):
        composite = f"{user_id}_{device_uuid}"
        if len(composite) != 73:
            _LOGGER.warning(
                "Composite client ID length %s (expected 73): %s",
                len(composite),
                composite,
            )
        return composite

    # Attempt reconstruction if user_id is 32 hex chars
    hex32_re = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
    if hex32_re.match(user_id) and uuid_re.match(device_uuid):
        user_id_formatted = f"{user_id[0:8]}-{user_id[8:12]}-{user_id[12:16]}-{user_id[16:20]}-{user_id[20:32]}"
        composite = f"{user_id_formatted}_{device_uuid}"
        if len(composite) != 73:
            _LOGGER.warning(
                "Reconstructed composite client ID length %s (expected 73): %s",
                len(composite),
                composite,
            )
        _LOGGER.info(
            "Reconstructed composite client ID from 32-hex user ID: %s", composite
        )
        return composite

    # Fallback
    return f"client-{device_uuid}"
