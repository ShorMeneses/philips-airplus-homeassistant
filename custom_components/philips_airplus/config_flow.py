"""Config flow for Philips Air+ integration."""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_NAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .auth import PhilipsAirplusAuth
from .api import PhilipsAirplusAPIClient, PhilipsAirplusDevice
from .auth import PhilipsAirplusOAuth2Implementation
from .const import (
    AUTH_MODE_OAUTH,
    CONF_AUTH_MODE,
    CONF_CLIENT_ID,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_UUID,
    CONF_ENABLE_MQTT,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_USER_ID,
    DEFAULT_CLIENT_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class PhilipsAirplusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Philips Air+."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._auth_mode: str = AUTH_MODE_OAUTH
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expires_at: Optional[int] = None
        self._devices: List[PhilipsAirplusDevice] = []
        self._auth: Optional[PhilipsAirplusAuth] = None
        self._client_id: Optional[str] = None
        self._oauth_flow_id: Optional[str] = None
        self._oauth_authorize_url: Optional[str] = None
        self._oauth_instructions: Optional[str] = None

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        self._client_id = DEFAULT_CLIENT_ID
        return await self.async_step_oauth()

    async def async_step_oauth(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle OAuth authentication."""
        errors: Dict[str, str] = {}

        try:
            # First call: generate authorize URL and show instructions
            if user_input is None:
                # create a flow-specific id and generate authorize URL with PKCE
                self._oauth_flow_id = secrets.token_urlsafe(8)
                impl = PhilipsAirplusOAuth2Implementation(self.hass, client_id=self._client_id)
                authorize_url = await impl.async_generate_authorize_url(self._oauth_flow_id)
                _LOGGER.debug("Generated authorize URL for flow %s: %s", getattr(self, "_oauth_flow_id", None), authorize_url)

                # Create a fully formatted instructions string and store it on the flow
                instructions = (
                    "1) Open the following URL in your browser:\n" + authorize_url + "\n\n"
                    "2) Log in and authorize the application.\n"
                    "3) You will be redirected to a page showing an authorization code.\n"
                    "4) Copy that code and paste it into the field below.\n"
                )
                self._oauth_authorize_url = authorize_url
                self._oauth_instructions = instructions

                return self.async_show_form(
                    step_id="oauth",
                    data_schema=vol.Schema({
                        vol.Required("auth_code"): str,
                    }),
                    description_placeholders={"instructions": instructions},
                )

            # When user submits the form with the authorization code
            auth_code = user_input.get("auth_code")
            if not auth_code:
                errors["base"] = "missing_code"
                return self.async_show_form(
                    step_id="oauth",
                    data_schema=vol.Schema({vol.Required("auth_code"): str}),
                    errors=errors,
                    description_placeholders={"instructions": getattr(self, "_oauth_instructions", "")},
                )

            # Exchange code for tokens
            impl = PhilipsAirplusOAuth2Implementation(self.hass, client_id=self._client_id)
            token_data = await impl.async_request_token(auth_code, getattr(self, "_oauth_flow_id", ""))
            access_token = token_data.get("access_token") or token_data.get("accessToken")
            refresh_token = token_data.get("refresh_token") or token_data.get("refreshToken")
            
            # Extract token expiration (exp claim or expires_in)
            token_expires_at = None
            exp = token_data.get("exp")
            expires_in = token_data.get("expires_in")
            if exp:
                token_expires_at = int(exp)
            elif expires_in:
                token_expires_at = int((datetime.now() + timedelta(seconds=int(expires_in))).timestamp())

            if not access_token:
                _LOGGER.error("Token response did not contain access_token: %s", token_data)
                errors["base"] = "invalid_token"
                return self.async_show_form(
                    step_id="oauth",
                    data_schema=vol.Schema({vol.Required("auth_code"): str}),
                    errors=errors,
                    description_placeholders={"instructions": getattr(self, "_oauth_instructions", "")},
                )

            # Validate token by listing devices
            api_client = PhilipsAirplusAPIClient(access_token)
            devices_data = await api_client.list_devices()
            await api_client.close()

            self._access_token = access_token
            self._refresh_token = refresh_token
            self._token_expires_at = token_expires_at
            self._devices = [PhilipsAirplusDevice(device_data) for device_data in devices_data]

            if not self._devices:
                errors["base"] = "no_devices"
                return self.async_show_form(
                    step_id="oauth",
                    data_schema=vol.Schema({vol.Required("auth_code"): str}),
                    errors=errors,
                    description_placeholders={"instructions": getattr(self, "_oauth_instructions", "")},
                )

            return await self.async_step_select_device()

        except Exception as ex:
            _LOGGER.exception("OAuth step failed: %s", ex)
            errors["base"] = "unknown"
            return self.async_show_form(
                step_id="oauth",
                data_schema=vol.Schema({vol.Required("auth_code"): str}),
                errors=errors,
                description_placeholders={"instructions": getattr(self, "_oauth_instructions", "")},
            )

    async def async_step_select_device(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle device selection."""
        if user_input is not None:
            device_index = user_input["device"]
            try:
                device_index_int = int(device_index)
            except Exception:
                _LOGGER.error("Invalid device index received: %s", device_index)
                return self.async_abort(reason="invalid_device")

            selected_device = self._devices[device_index_int]
            
            self._auth = PhilipsAirplusAuth(
                self.hass,
                auth_mode=AUTH_MODE_OAUTH,
                access_token=self._access_token,
            )
            self._auth._client_id = self._client_id
            
            if await self._auth.initialize():
                data = {
                    CONF_AUTH_MODE: self._auth_mode,
                    CONF_ACCESS_TOKEN: self._access_token,
                    CONF_REFRESH_TOKEN: self._refresh_token,
                    CONF_TOKEN_EXPIRES_AT: self._token_expires_at,
                    CONF_DEVICE_ID: selected_device.uuid,
                    CONF_DEVICE_UUID: selected_device.uuid,
                    CONF_DEVICE_NAME: selected_device.name,
                    CONF_USER_ID: self._auth.user_id,
                    CONF_CLIENT_ID: self._client_id,
                }
                
                await self._auth.close()
                
                return self.async_create_entry(
                    title=selected_device.name,
                    data=data
                )
            else:
                return self.async_abort(reason="auth_failed")

        # Create device selection options mapping key -> label
        device_options = {
            str(index): f"{device.name} ({device.type})"
            for index, device in enumerate(self._devices)
        }

        _LOGGER.debug("Device options for selection: %s", device_options)

        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema({
                vol.Required("device"): vol.In(device_options),
            }),
        )

    async def async_step_reauth(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle reauthentication."""
        return await self.async_step_user()

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return PhilipsAirplusOptionsFlowHandler(config_entry)


class PhilipsAirplusOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Philips Air+."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            # Handle options update
            return self.async_create_entry(title="", data=user_input)

        # Show current configuration
        auth_mode = self.config_entry.data.get(CONF_AUTH_MODE, AUTH_MODE_OAUTH)
        device_name = self.config_entry.data.get(CONF_DEVICE_NAME, "Unknown")
        enable_mqtt = self.config_entry.options.get(CONF_ENABLE_MQTT, True)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_ENABLE_MQTT, default=enable_mqtt): bool,
            }),
            description_placeholders={
                "auth_mode": "OAuth",
                "device_name": device_name,
            },
        )