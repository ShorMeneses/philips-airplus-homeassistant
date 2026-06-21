"""Config flow for Philips Air+ integration."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.data_entry_flow import FlowResult

from .auth import PhilipsAirplusAuth, PhilipsAirplusOAuth2Implementation
from .api import PhilipsAirplusAPIClient, PhilipsAirplusDevice
from .email_auth import EmailOTPAuth, EmailOTPAuthError
from .const import (
    AUTH_MODE_OAUTH,
    AUTH_MODE_EMAIL_OTP,
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
    OAUTH_CLIENT_ID_HOMEID,
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
        self._reauth_entry: Optional[config_entries.ConfigEntry] = None
        self._email: Optional[str] = None
        self._vtoken: Optional[str] = None

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        return await self.async_step_auth_method()

    async def async_step_auth_method(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Choose authentication method."""
        if self._reauth_entry:
            # For reauth, use the entry's stored auth_mode
            original_mode = self._reauth_entry.data.get(
                CONF_AUTH_MODE, AUTH_MODE_OAUTH
            )
            if original_mode == AUTH_MODE_EMAIL_OTP:
                self._auth_mode = AUTH_MODE_EMAIL_OTP
                self._client_id = DEFAULT_CLIENT_ID
                return await self.async_step_email()
            self._auth_mode = AUTH_MODE_OAUTH
            self._client_id = DEFAULT_CLIENT_ID
            return await self.async_step_oauth()

        if user_input is not None:
            method = user_input.get("auth_method", AUTH_MODE_OAUTH)
            if method == AUTH_MODE_EMAIL_OTP:
                self._auth_mode = AUTH_MODE_EMAIL_OTP
                self._client_id = DEFAULT_CLIENT_ID
                return await self.async_step_email()
            self._auth_mode = AUTH_MODE_OAUTH
            self._client_id = DEFAULT_CLIENT_ID
            return await self.async_step_oauth()

        return self.async_show_form(
            step_id="auth_method",
            data_schema=vol.Schema(
                {
                    vol.Required("auth_method", default=AUTH_MODE_OAUTH): vol.In(
                        {
                            AUTH_MODE_OAUTH: "OAuth PKCE (browser DevTools)",
                            AUTH_MODE_EMAIL_OTP: "Email + verification code",
                        }
                    ),
                }
            ),
        )

    async def async_step_email(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Enter email address and request OTP."""
        errors: Dict[str, str] = {}
        if user_input is not None:
            email = (user_input.get("email") or "").strip()
            if not email or "@" not in email:
                errors["base"] = "invalid_email"
            else:
                self._email = email
                try:
                    email_auth = EmailOTPAuth(self.hass)
                    self._vtoken = await email_auth.request_otp(email)
                    await email_auth.close()
                    return await self.async_step_email_otp()
                except EmailOTPAuthError as ex:
                    _LOGGER.error("OTP send failed: %s", ex)
                    errors["base"] = "otp_send_failed"

        return self.async_show_form(
            step_id="email",
            data_schema=vol.Schema(
                {
                    vol.Required("email"): str,
                }
            ),
            description_placeholders={"email": self._email or ""},
            errors=errors,
        )

    async def async_step_email_otp(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Enter OTP code and complete authentication."""
        errors: Dict[str, str] = {}
        if user_input is not None:
            code = (user_input.get("otp_code") or "").strip()
            if not code:
                errors["base"] = "missing_code"
            else:
                try:
                    email_auth = EmailOTPAuth(self.hass)
                    session_token = await email_auth.verify_otp(
                        self._email or "", code, self._vtoken or ""
                    )
                    token_data = await email_auth.exchange_session_for_tokens(
                        session_token
                    )

                    access_token = token_data.get("access_token")
                    refresh_token = token_data.get("refresh_token")
                    token_expires_at = token_data.get("token_expires_at")

                    if not access_token:
                        errors["base"] = "invalid_token"
                        return self.async_show_form(
                            step_id="email_otp",
                            data_schema=vol.Schema(
                                {vol.Required("otp_code"): str}
                            ),
                            description_placeholders={
                                "email": self._email or ""
                            },
                            errors=errors,
                        )

                    # Validate token by listing devices
                    api_client = PhilipsAirplusAPIClient(access_token)
                    devices_data = await api_client.list_devices()
                    await api_client.close()
                    _LOGGER.info("IoT device list returned %d device(s)", len(devices_data))

                    # Fallback: try HomeID client if Air+ tokens returned no devices
                    if not devices_data:
                        _LOGGER.info(
                            "IoT device list empty with Air+ tokens, trying HomeID client fallback"
                        )
                        homeid_tokens = await email_auth.exchange_session_for_tokens_homeid_fallback()
                        if homeid_tokens:
                            homeid_access = homeid_tokens.get("access_token")
                            if homeid_access:
                                # Use HomeID tokens for device listing
                                api_client2 = PhilipsAirplusAPIClient(homeid_access)
                                devices_data = await api_client2.list_devices()
                                await api_client2.close()
                                _LOGGER.info(
                                    "IoT device list with HomeID tokens: %d device(s)",
                                    len(devices_data),
                                )
                                if not devices_data:
                                    # Last resort: HomeID backend appliance API
                                    devices_data = await email_auth.list_devices_via_homeid(
                                        homeid_access
                                    )
                                if devices_data:
                                    # Use HomeID tokens going forward
                                    access_token = homeid_access
                                    refresh_token = homeid_tokens.get("refresh_token")
                                    token_expires_at = homeid_tokens.get("token_expires_at")
                                    self._client_id = OAUTH_CLIENT_ID_HOMEID
                                    self._auth_mode = AUTH_MODE_EMAIL_OTP

                    await email_auth.close()

                    self._access_token = access_token
                    self._refresh_token = refresh_token
                    self._token_expires_at = token_expires_at
                    self._devices = [
                        PhilipsAirplusDevice(d)
                        for d in devices_data
                    ]

                    if not self._devices:
                        errors["base"] = "no_devices"
                        return self.async_show_form(
                            step_id="email_otp",
                            data_schema=vol.Schema(
                                {vol.Required("otp_code"): str}
                            ),
                            description_placeholders={
                                "email": self._email or ""
                            },
                            errors=errors,
                        )

                    return await self.async_step_select_device()

                except EmailOTPAuthError as ex:
                    _LOGGER.error("Email OTP auth failed: %s", ex)
                    errors["base"] = "otp_verify_failed"

        return self.async_show_form(
            step_id="email_otp",
            data_schema=vol.Schema(
                {
                    vol.Required("otp_code"): str,
                }
            ),
            description_placeholders={"email": self._email or ""},
            errors=errors,
        )

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
                impl = PhilipsAirplusOAuth2Implementation(
                    self.hass, client_id=self._client_id
                )
                authorize_url = await impl.async_generate_authorize_url(
                    self._oauth_flow_id
                )
                _LOGGER.debug(
                    "Generated authorize URL for flow %s: %s",
                    getattr(self, "_oauth_flow_id", None),
                    authorize_url,
                )

                self._oauth_authorize_url = authorize_url

                return self.async_show_form(
                    step_id="oauth",
                    data_schema=vol.Schema(
                        {
                            vol.Required("auth_code"): str,
                        }
                    ),
                    description_placeholders={"authorize_url": authorize_url},
                )

            # When user submits the form with the authorization code
            auth_code = user_input.get("auth_code")
            if not auth_code:
                errors["base"] = "missing_code"
                return self.async_show_form(
                    step_id="oauth",
                    data_schema=vol.Schema({vol.Required("auth_code"): str}),
                    errors=errors,
                    description_placeholders={
                        "authorize_url": getattr(self, "_oauth_authorize_url", "")
                    },
                )

            # Exchange code for tokens
            impl = PhilipsAirplusOAuth2Implementation(
                self.hass, client_id=self._client_id
            )
            token_data = await impl.async_request_token(
                auth_code, getattr(self, "_oauth_flow_id", "")
            )
            access_token = token_data.get("access_token") or token_data.get(
                "accessToken"
            )
            refresh_token = token_data.get("refresh_token") or token_data.get(
                "refreshToken"
            )

            # Extract token expiration (exp claim or expires_in)
            token_expires_at = None
            exp = token_data.get("exp")
            expires_in = token_data.get("expires_in")
            if exp:
                token_expires_at = int(exp)
            elif expires_in:
                token_expires_at = int(
                    (datetime.now() + timedelta(seconds=int(expires_in))).timestamp()
                )

            if not access_token:
                _LOGGER.error(
                    "Token response did not contain access_token: %s", token_data
                )
                errors["base"] = "invalid_token"
                return self.async_show_form(
                    step_id="oauth",
                    data_schema=vol.Schema({vol.Required("auth_code"): str}),
                    errors=errors,
                    description_placeholders={
                        "authorize_url": getattr(self, "_oauth_authorize_url", "")
                    },
                )

            # Validate token by listing devices
            api_client = PhilipsAirplusAPIClient(access_token)
            devices_data = await api_client.list_devices()
            await api_client.close()

            self._access_token = access_token
            self._refresh_token = refresh_token
            self._token_expires_at = token_expires_at
            self._devices = [
                PhilipsAirplusDevice(device_data) for device_data in devices_data
            ]

            if not self._devices:
                errors["base"] = "no_devices"
                return self.async_show_form(
                    step_id="oauth",
                    data_schema=vol.Schema({vol.Required("auth_code"): str}),
                    errors=errors,
                    description_placeholders={
                        "authorize_url": getattr(self, "_oauth_authorize_url", "")
                    },
                )

            return await self.async_step_select_device()

        except Exception as ex:
            _LOGGER.exception("OAuth step failed: %s", ex)
            errors["base"] = "unknown"
            return self.async_show_form(
                step_id="oauth",
                data_schema=vol.Schema({vol.Required("auth_code"): str}),
                errors=errors,
                description_placeholders={
                    "authorize_url": getattr(self, "_oauth_authorize_url", "")
                },
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
                auth_mode=self._auth_mode,
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

                if self._reauth_entry:
                    # Update existing entry
                    self.hass.config_entries.async_update_entry(
                        self._reauth_entry, data=data
                    )
                    self.hass.async_create_task(
                        self.hass.config_entries.async_reload(
                            self._reauth_entry.entry_id
                        )
                    )
                    return self.async_abort(reason="reauth_successful")

                return self.async_create_entry(title=selected_device.name, data=data)
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
            data_schema=vol.Schema(
                {
                    vol.Required("device"): vol.In(device_options),
                }
            ),
        )

    async def async_step_reauth(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle reauthentication."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_auth_method()

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
        self._entry = config_entry
        self._client_id: Optional[str] = config_entry.data.get(
            CONF_CLIENT_ID, DEFAULT_CLIENT_ID
        )
        self._auth_mode = config_entry.data.get(CONF_AUTH_MODE, AUTH_MODE_OAUTH)
        self._oauth_flow_id: Optional[str] = None
        self._oauth_authorize_url: Optional[str] = None
        self._email: Optional[str] = None
        self._vtoken: Optional[str] = None

    def _build_init_schema(self, enable_mqtt: bool, auth_code: str = "") -> vol.Schema:
        """Build options form schema."""
        if self._auth_mode == AUTH_MODE_EMAIL_OTP:
            return vol.Schema(
                {
                    vol.Optional(CONF_ENABLE_MQTT, default=enable_mqtt): bool,
                    vol.Optional("email", default=""): str,
                }
            )
        return vol.Schema(
            {
                vol.Optional(CONF_ENABLE_MQTT, default=enable_mqtt): bool,
                vol.Optional("auth_code", default=auth_code): str,
            }
        )

    async def _async_show_init_form(
        self,
        enable_mqtt: bool,
        auth_code: str = "",
        errors: Optional[Dict[str, str]] = None,
    ) -> FlowResult:
        """Render options form with current placeholders."""
        device_name = self._entry.data.get(CONF_DEVICE_NAME, "Unknown")

        if self._auth_mode == AUTH_MODE_EMAIL_OTP:
            return self.async_show_form(
                step_id="init",
                data_schema=self._build_init_schema(enable_mqtt),
                errors=errors or {},
                description_placeholders={
                    "device_name": device_name,
                    "reauth_instructions": (
                        "This integration was set up with email authentication. "
                        "To re-authenticate, enter your email below to receive a "
                        "new verification code."
                    ),
                },
            )

        if not self._oauth_flow_id or not self._oauth_authorize_url:
            self._oauth_flow_id = secrets.token_urlsafe(8)
            impl = PhilipsAirplusOAuth2Implementation(
                self.hass, client_id=self._client_id
            )
            self._oauth_authorize_url = await impl.async_generate_authorize_url(self._oauth_flow_id)

        return self.async_show_form(
            step_id="init",
            data_schema=self._build_init_schema(enable_mqtt, auth_code),
            errors=errors or {},
            description_placeholders={
                "device_name": device_name,
                "reauth_instructions": (
                    "To re-authenticate via OAuth:\n\n"
                    "1) Open this URL in your browser:\n"
                    f"{self._oauth_authorize_url}\n\n"
                    "2) Open DevTools (F12) → Network tab before logging in.\n"
                    "3) Complete Philips login and authorize the app.\n"
                    "4) Find the `com.philips.air://loginredirect?code=...` "
                    "redirect and copy the full URL.\n"
                    "5) Paste it into the field below."
                ),
            },
        )

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options."""
        enable_mqtt = self._entry.options.get(CONF_ENABLE_MQTT, True)

        if user_input is None:
            return await self._async_show_init_form(enable_mqtt)

        enable_mqtt = user_input.get(CONF_ENABLE_MQTT, enable_mqtt)

        if self._auth_mode == AUTH_MODE_EMAIL_OTP:
            email = (user_input.get("email") or "").strip()
            if email:
                self._email = email
                try:
                    email_auth = EmailOTPAuth(self.hass)
                    self._vtoken = await email_auth.request_otp(email)
                    await email_auth.close()
                    return await self.async_step_email_otp_options()
                except EmailOTPAuthError as ex:
                    _LOGGER.error("Options OTP send failed: %s", ex)
                    return await self._async_show_init_form(
                        enable_mqtt,
                        errors={"base": "otp_send_failed"},
                    )
            return self.async_create_entry(title="", data={CONF_ENABLE_MQTT: enable_mqtt})

        auth_code = (user_input.get("auth_code") or "").strip()

        if auth_code:
            try:
                if not self._oauth_flow_id:
                    return await self._async_show_init_form(
                        enable_mqtt,
                        auth_code="",
                        errors={"base": "auth_failed"},
                    )

                impl = PhilipsAirplusOAuth2Implementation(
                    self.hass, client_id=self._client_id
                )
                token_data = await impl.async_request_token(
                    auth_code, self._oauth_flow_id
                )

                access_token = token_data.get("access_token") or token_data.get(
                    "accessToken"
                )
                refresh_token = token_data.get("refresh_token") or token_data.get(
                    "refreshToken"
                )
                exp = token_data.get("exp")
                expires_in = token_data.get("expires_in")
                token_expires_at = None
                if exp:
                    token_expires_at = int(exp)
                elif expires_in:
                    token_expires_at = int(
                        (
                            datetime.now() + timedelta(seconds=int(expires_in))
                        ).timestamp()
                    )

                if not access_token:
                    _LOGGER.error(
                        "Options reauth token response missing access_token: %s",
                        token_data,
                    )
                    return await self._async_show_init_form(
                        enable_mqtt,
                        auth_code="",
                        errors={"base": "invalid_token"},
                    )

                auth = PhilipsAirplusAuth(
                    self.hass,
                    auth_mode=AUTH_MODE_OAUTH,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    client_id=self._client_id,
                )
                auth_ok = await auth.initialize()
                user_id = auth.user_id
                await auth.close()

                if not auth_ok:
                    return await self._async_show_init_form(
                        enable_mqtt,
                        auth_code="",
                        errors={"base": "auth_failed"},
                    )

                updated_data = {**self._entry.data}
                updated_data[CONF_ACCESS_TOKEN] = access_token
                updated_data[CONF_REFRESH_TOKEN] = refresh_token
                updated_data[CONF_TOKEN_EXPIRES_AT] = token_expires_at
                updated_data[CONF_USER_ID] = user_id
                updated_data[CONF_CLIENT_ID] = self._client_id
                self.hass.config_entries.async_update_entry(
                    self._entry, data=updated_data
                )
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self._entry.entry_id)
                )
                _LOGGER.info(
                    "Options re-authentication succeeded and entry was reloaded"
                )
            except Exception as ex:
                _LOGGER.exception("Options re-authentication failed: %s", ex)
                return await self._async_show_init_form(
                    enable_mqtt,
                    auth_code="",
                    errors={"base": "auth_failed"},
                )

        return self.async_create_entry(title="", data={CONF_ENABLE_MQTT: enable_mqtt})

    async def async_step_email_otp_options(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Enter OTP code for options re-authentication."""
        errors: Dict[str, str] = {}
        if user_input is not None:
            code = (user_input.get("otp_code") or "").strip()
            if not code:
                errors["base"] = "missing_code"
            else:
                try:
                    email_auth = EmailOTPAuth(self.hass)
                    session_token = await email_auth.verify_otp(
                        self._email or "", code, self._vtoken or ""
                    )
                    token_data = await email_auth.exchange_session_for_tokens(
                        session_token
                    )

                    access_token = token_data.get("access_token")
                    refresh_token = token_data.get("refresh_token")
                    token_expires_at = token_data.get("token_expires_at")

                    await email_auth.close()

                    if not access_token:
                        errors["base"] = "invalid_token"

                    if not errors:
                        auth = PhilipsAirplusAuth(
                            self.hass,
                            auth_mode=AUTH_MODE_EMAIL_OTP,
                            access_token=access_token,
                            refresh_token=refresh_token,
                            client_id=self._client_id,
                        )
                        auth_ok = await auth.initialize()
                        user_id = auth.user_id
                        await auth.close()

                        if auth_ok:
                            updated_data = {**self._entry.data}
                            updated_data[CONF_ACCESS_TOKEN] = access_token
                            updated_data[CONF_REFRESH_TOKEN] = refresh_token
                            updated_data[CONF_TOKEN_EXPIRES_AT] = token_expires_at
                            updated_data[CONF_USER_ID] = user_id
                            updated_data[CONF_CLIENT_ID] = self._client_id
                            self.hass.config_entries.async_update_entry(
                                self._entry, data=updated_data
                            )
                            self.hass.async_create_task(
                                self.hass.config_entries.async_reload(
                                    self._entry.entry_id
                                )
                            )
                            _LOGGER.info(
                                "Options email re-authentication succeeded and entry was reloaded"
                            )
                            return self.async_create_entry(
                                title="",
                                data={
                                    CONF_ENABLE_MQTT: self._entry.options.get(
                                        CONF_ENABLE_MQTT, True
                                    )
                                },
                            )
                        errors["base"] = "auth_failed"

                except EmailOTPAuthError as ex:
                    _LOGGER.error("Options email OTP auth failed: %s", ex)
                    errors["base"] = "otp_verify_failed"
                except Exception as ex:
                    _LOGGER.exception("Options email OTP reauth failed: %s", ex)
                    errors["base"] = "auth_failed"

        return self.async_show_form(
            step_id="email_otp_options",
            data_schema=vol.Schema(
                {
                    vol.Required("otp_code"): str,
                }
            ),
            description_placeholders={"email": self._email or ""},
            errors=errors,
        )
