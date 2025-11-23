"""Data coordinator for Philips Air+ integration."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    PhilipsAirplusAPIClient,
    PhilipsAirplusDevice,
    build_client_id
)
from .auth import PhilipsAirplusAuth
from .const import (
    AUTH_MODE_OAUTH,
    CONF_ACCESS_TOKEN,
    CONF_AUTH_MODE,
    CONF_CLIENT_ID,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_UUID,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    DEFAULT_CLIENT_ID,
    PORT_CONFIG,
    PORT_FILTER_READ,
    PORT_STATUS,
    PRESET_MODE_MANUAL,
    PRESET_MODE_SLEEP,
    PRESET_MODE_TURBO,
    PROP_FAN_SPEED,
    PROP_FILTER_CLEAN_NOMINAL,
    PROP_FILTER_CLEAN_REMAINING,
    PROP_FILTER_REPLACE_NOMINAL,
    PROP_FILTER_REPLACE_REMAINING,
    PROP_MODE,
    PROP_POWER_FLAG,
    SCAN_INTERVAL,
    TOKEN_REFRESH_BUFFER,
    TOPIC_CONTROL_TEMPLATE,
    TOPIC_SHADOW_UPDATE_TEMPLATE,
    TOPIC_STATUS_TEMPLATE,
)
from .mqtt_client import PhilipsAirplusMQTTClient
from .model_manager import PhilipsAirplusModelManager

_LOGGER = logging.getLogger(__name__)

class PhilipsAirplusDataCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Data coordinator for Philips Air+ device."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=entry.title,
            update_interval=SCAN_INTERVAL,
        )
        
        self.entry = entry
        self._device_id = entry.data[CONF_DEVICE_ID]
        self._device_name = entry.data[CONF_DEVICE_NAME]
        self._device_uuid = entry.data[CONF_DEVICE_UUID]
        
        # Initialize authentication
        self._auth = PhilipsAirplusAuth(
            hass=hass,
            auth_mode=entry.data[CONF_AUTH_MODE],
            access_token=entry.data.get(CONF_ACCESS_TOKEN),
            refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
            client_id=entry.data.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
            token_callback=self._on_token_refresh,
        )
        
        # Load stored token expiration if available
        token_expires_at = entry.data.get(CONF_TOKEN_EXPIRES_AT)
        if token_expires_at:
            self._auth.expires_at = datetime.fromtimestamp(int(token_expires_at))
            _LOGGER.debug("Loaded token expiration from config: %s", self._auth.expires_at)
        
        # Initialize API client
        self._api_client = PhilipsAirplusAPIClient(self._auth.access_token or "")
        
        # Initialize Model Manager
        component_path = os.path.dirname(__file__)
        self._model_manager = PhilipsAirplusModelManager(component_path)

        # Initialize MQTT client
        self._mqtt_client: Optional[PhilipsAirplusMQTTClient] = None
        
        # Device state
        self._device_state: Dict[str, Any] = {}
        self._filter_data: Dict[str, Any] = {}


        # Load model config (defaulting to AC0650/10 until we know better)
        # We might update this once we get the model from the device
        self._model_config = self._model_manager.get_model_config("AC0650/10")
        
        # Connection status
        self._connected = False
        self._last_update: Optional[datetime] = None
        self._last_full_request: Optional[datetime] = None
        
    async def _on_token_refresh(self, token_data: Dict[str, Any]) -> None:
        """Handle token refresh events."""
        _LOGGER.debug("Token refreshed, updating config entry")
        
        # Update config entry with new token data
        new_data = {**self.entry.data}
        new_data[CONF_ACCESS_TOKEN] = token_data.get("access_token")
        new_data[CONF_REFRESH_TOKEN] = token_data.get("refresh_token")
        new_data[CONF_TOKEN_EXPIRES_AT] = token_data.get("expires_at")
        
        self.hass.config_entries.async_update_entry(
            self.entry,
            data=new_data
        )
        _LOGGER.info("Config entry updated with new tokens")

    @property
    def device_id(self) -> str:
        """Get device ID."""
        return self._device_id

    @property
    def device_name(self) -> str:
        """Get device name."""
        return self._device_name

    @property
    def device_uuid(self) -> str:
        """Get device UUID."""
        return self._device_uuid

    @property
    def is_connected(self) -> bool:
        """Check if device is connected."""
        return self._connected

    @property
    def device_state(self) -> Dict[str, Any]:
        """Get device state."""
        return self._device_state

    @property
    def filter_data(self) -> Dict[str, Any]:
        """Get filter data."""
        return self._filter_data



    async def _async_setup(self) -> None:
        """Set up the coordinator."""
        try:
            # Initialize authentication
            if not await self._auth.initialize():
                raise ConfigEntryAuthFailed("Failed to initialize authentication")
            
            # Update API client with potentially refreshed token
            self._api_client = PhilipsAirplusAPIClient(self._auth.access_token or "")
            
            # Build client ID for MQTT
            client_id = build_client_id(
                self._auth.user_id or "",
                self._device_uuid
            )
            
            # Initialize MQTT client
            self._mqtt_client = PhilipsAirplusMQTTClient(
                device_id=self._device_id,
                access_token=self._auth.access_token or "",
                signature=self._auth.signature or "",
                client_id=client_id
            )
            
            # Set up MQTT callbacks
            self._mqtt_client.set_message_callback(self._on_mqtt_message)
            self._mqtt_client.set_connection_callback(self._on_mqtt_connection)
            
            # Connect to MQTT asynchronously (avoid blocking loop)
            if not await self._mqtt_client.async_connect():
                raise UpdateFailed("Failed to connect to MQTT")
            
            # Request initial device status
            await asyncio.sleep(1)  # Give MQTT connection time to establish
            # Schedule initial status request (it is now async)
            self.hass.async_create_task(self._request_initial_status())
            
        except Exception as ex:
            raise UpdateFailed(f"Failed to set up coordinator: {ex}") from ex

    async def _request_initial_status(self) -> None:
        """Request initial device status."""
        if not self._mqtt_client or not self._mqtt_client.is_connected():
            return
        now = datetime.now()
        # Throttle full multi-port requests; only perform if first time or >5 minutes since last full
        if not self._last_full_request or (now - self._last_full_request) > timedelta(minutes=5):
            self._last_full_request = now
            self._mqtt_client.request_port_status(PORT_STATUS)
            await asyncio.sleep(0.1)
            self._mqtt_client.request_port_status(PORT_CONFIG)
            await asyncio.sleep(0.1)
            self._mqtt_client.request_port_status(PORT_FILTER_READ)
            await asyncio.sleep(0.1)
            self._mqtt_client.request_shadow_get()
        else:
            # Only request Status port for lightweight refresh
            self._mqtt_client.request_port_status(PORT_STATUS)

    def _on_mqtt_connection(self, connected: bool) -> None:
        """Handle MQTT connection status changes."""
        # This runs in the MQTT client thread, so we must schedule the update
        # on the main event loop to safely interact with HA.
        self.hass.loop.call_soon_threadsafe(self._on_mqtt_connection_in_loop, connected)

    def _on_mqtt_connection_in_loop(self, connected: bool) -> None:
        """Handle MQTT connection status changes (in event loop)."""
        self._connected = connected
        if connected:
            _LOGGER.info("Connected to Philips Air+ device %s", self._device_name)
            # Schedule the async initial status request
            self.hass.async_create_task(self._request_initial_status())
        else:
            _LOGGER.warning("Disconnected from Philips Air+ device %s", self._device_name)
            # Schedule automatic reconnection attempt after 30 seconds
            async def _reconnect_later():
                await asyncio.sleep(30)
                if not self._connected and self._mqtt_client:
                    _LOGGER.info("Attempting to reconnect MQTT for %s", self._device_name)
                    try:
                        if await self._mqtt_client.async_connect():
                            _LOGGER.info("MQTT reconnection successful for %s", self._device_name)
                        else:
                            _LOGGER.warning("MQTT reconnection failed for %s", self._device_name)
                    except Exception as ex:
                        _LOGGER.error("Error during MQTT reconnection: %s", ex)
            
            self.hass.async_create_task(_reconnect_later())
            
        # Trigger update so 'available' state is refreshed immediately
        self.async_set_updated_data({
            "device_state": self._device_state,
            "filter_data": self._filter_data,
            "filter_info": self._get_filter_info() or {},
            "connected": self._connected,
            "last_update": self._last_update,
        })

    def _on_mqtt_message(self, message_data: Dict[str, Any]) -> None:
        """Handle incoming MQTT messages."""
        # This runs in the MQTT client thread, so we must schedule the update
        # on the main event loop to safely interact with HA.
        self.hass.loop.call_soon_threadsafe(self._on_mqtt_message_in_loop, message_data)

    def _on_mqtt_message_in_loop(self, message_data: Dict[str, Any]) -> None:
        """Handle incoming MQTT messages (in event loop)."""
        try:
            self._last_update = datetime.now()            
            command = message_data.get("cn")
            data = message_data.get("data", {})
            # Some responses (e.g., getAllPorts) return a list of port descriptors
            if isinstance(data, list):
                # Extract port names for diagnostics and later use
                ports = [item.get("portName") for item in data if isinstance(item, dict) and "portName" in item]
                _LOGGER.debug("Received list-style MQTT data (getAllPorts): %s", ports)

                return

            port_name = data.get("portName") if isinstance(data, dict) else None
            properties = data.get("properties", {}) if isinstance(data, dict) else {}
            
            _LOGGER.debug("Received MQTT message: %s", message_data)
            
            # Fallback: if portName is missing but we have properties, try to process as status
            if not port_name and properties:
                _LOGGER.debug("Message missing portName, attempting to process as status update")
                self._process_status_update(properties)
            elif port_name == PORT_STATUS and properties:
                self._process_status_update(properties)
            elif port_name == PORT_CONFIG and properties:
                self._process_config_update(properties)
            elif port_name == PORT_FILTER_READ and properties:
                self._process_filter_update(properties)
            
        except Exception as ex:
            _LOGGER.error("Error processing MQTT message: %s", ex)

    def _process_status_update(self, properties: Dict[str, Any]) -> None:
        """Process status update."""
        self._device_state.update(properties)
        
        # Log important changes
        prop_fan_speed = self._model_config.get("properties", {}).get("fan_speed")
        prop_mode = self._model_config.get("properties", {}).get("mode")
        
        if prop_fan_speed and prop_fan_speed in properties:
            _LOGGER.debug("Fan speed updated: %s", properties[prop_fan_speed])
        if prop_mode and prop_mode in properties:
            mode_value = properties[prop_mode]
            mode_name = self._get_mode_name(mode_value)
            _LOGGER.debug("Mode updated: %s (%s)", mode_name, mode_value)

        # Trigger coordinator update so entities refresh state in HA
        self.async_set_updated_data({
            "device_state": self._device_state,
            "filter_data": self._filter_data,
            "filter_info": self._get_filter_info() or {},
            "connected": self._connected,
            "last_update": self._last_update,
        })

    def _process_config_update(self, properties: Dict[str, Any]) -> None:
        """Process config update."""
        if "ctn" in properties:
            model = properties["ctn"]
            _LOGGER.debug("Device model reported: %s", model)
            # Update model config if it changed
            self._model_config = self._model_manager.get_model_config(model)

    def _process_filter_update(self, properties: Dict[str, Any]) -> None:
        """Process filter update."""
        self._filter_data.update(properties)
        
        # Calculate filter percentages
        filter_info = self._get_filter_info()
        if filter_info:
            _LOGGER.debug("Filter info: %s", filter_info)
        # Trigger coordinator update so sensor entities refresh immediately
        self.async_set_updated_data({
            "device_state": self._device_state,
            "filter_data": self._filter_data,
            "filter_info": filter_info or {},
            "connected": self._connected,
            "last_update": self._last_update,
        })



    def _get_mode_name(self, mode_value: int) -> str:
        """Get mode name from value."""
        name = self._model_manager.get_mode_name(self._model_config.get("name", "AC0650/10"), mode_value)
        return name or PRESET_MODE_MANUAL

    def _get_filter_info(self) -> Optional[Dict[str, Any]]:
        """Get filter information."""
        filter_info = {}
        
        # Replace filter
        prop_replace_nom = self._model_config.get("properties", {}).get("filter_replace_nominal")
        prop_replace_rem = self._model_config.get("properties", {}).get("filter_replace_remaining")
        
        nominal_replace = self._filter_data.get(prop_replace_nom) if prop_replace_nom else None
        remaining_replace = self._filter_data.get(prop_replace_rem) if prop_replace_rem else None
        
        if nominal_replace and remaining_replace is not None and nominal_replace > 0:
            replace_percentage = round((remaining_replace / nominal_replace) * 100, 1)
            filter_info["replace_percentage"] = replace_percentage
            filter_info["replace_hours_remaining"] = remaining_replace
            filter_info["replace_hours_total"] = nominal_replace
        
        # Clean filter
        prop_clean_nom = self._model_config.get("properties", {}).get("filter_clean_nominal")
        prop_clean_rem = self._model_config.get("properties", {}).get("filter_clean_remaining")
        
        nominal_clean = self._filter_data.get(prop_clean_nom) if prop_clean_nom else None
        remaining_clean = self._filter_data.get(prop_clean_rem) if prop_clean_rem else None
        
        if nominal_clean and remaining_clean is not None and nominal_clean > 0:
            clean_percentage = round((remaining_clean / nominal_clean) * 100, 1)
            filter_info["clean_percentage"] = clean_percentage
            filter_info["clean_hours_remaining"] = remaining_clean
            filter_info["clean_hours_total"] = nominal_clean
        
        return filter_info if filter_info else None

    async def _async_update_data(self) -> Dict[str, Any]:
        """Update device data."""
        if not self._mqtt_client or not self._mqtt_client.is_connected():
            raise UpdateFailed("MQTT client not connected")
        
        # Ensure token is valid before request
        if await self._auth.ensure_access_token():
             # If token was refreshed, update MQTT credentials
             if self._mqtt_client and self._mqtt_client.access_token != self._auth.access_token:
                 _LOGGER.info("Token refreshed, updating MQTT credentials")
                 await self._mqtt_client.async_update_credentials(
                     self._auth.access_token,
                     self._auth.signature
                 )

        # Request status update
        self._mqtt_client.request_port_status(PORT_STATUS)
        
        # Return combined data
        return {
            "device_state": self._device_state,
            "filter_data": self._filter_data,
            "filter_info": self._get_filter_info() or {},
            "connected": self._connected,
            "last_update": self._last_update,
        }

    async def set_fan_speed(self, speed: int) -> bool:
        """Set fan speed."""
        if not self._mqtt_client or not self._mqtt_client.is_connected():
            return False
        
        # Get raw key for fan speed
        raw_key = self._model_config.get("properties", {}).get(PROP_FAN_SPEED)
        if not raw_key:
            _LOGGER.error("No raw key found for fan_speed")
            return False
            
        return self._mqtt_client.set_fan_speed(speed, raw_key=raw_key)

    async def set_mode(self, mode: str) -> bool:
        """Set device mode."""
        if not self._mqtt_client or not self._mqtt_client.is_connected():
            return False
        
        mode_map = self._model_config.get("modes", {})
        mode_value = mode_map.get(mode)
        
        _LOGGER.debug("Setting mode to %s (value=%s)", mode, mode_value)
        if mode_value is None:
            _LOGGER.error("Unknown mode: %s", mode)
            return False
        
        # Get raw key for mode
        raw_key = self._model_config.get("properties", {}).get(PROP_MODE)
        if not raw_key:
            _LOGGER.error("No raw key found for mode")
            return False
            
        return self._mqtt_client.set_mode(mode_value, raw_key=raw_key)

    async def set_power(self, power_on: bool) -> bool:
        """Set power state."""
        if not self._mqtt_client or not self._mqtt_client.is_connected():
            return False
        
        # Get raw key for fan speed (fallback)
        raw_speed_key = self._model_config.get("properties", {}).get(PROP_FAN_SPEED)
        
        # Get raw key for power flag (preferred)
        raw_power_key = self._model_config.get("properties", {}).get(PROP_POWER_FLAG)
        
        if not raw_speed_key and not raw_power_key:
            _LOGGER.error("No raw keys found for power control")
            return False
            
        return self._mqtt_client.set_power(
            power_on, 
            raw_speed_key=raw_speed_key,
            raw_power_key=raw_power_key
        )

    async def async_request_refresh(self) -> None:
        """Request refresh of device data."""
        if self._mqtt_client and self._mqtt_client.is_connected():
            # Use throttled status request rather than full multi-port
            await self._request_initial_status()
        
        await super().async_request_refresh()

    async def async_shutdown(self) -> None:
        """Shutdown coordinator."""
        if self._mqtt_client:
            self._mqtt_client.disconnect()
        
        if self._auth:
            await self._auth.close()
        
        if self._api_client:
            await self._api_client.close()

    async def async_setup(self) -> None:
        """Set up the coordinator."""
        await self._async_setup()
