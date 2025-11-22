"""Fan entity for Philips Air+ integration."""
from __future__ import annotations

import logging
import math
from typing import Optional, Any

from homeassistant.components.fan import (
    FanEntity,
    FanEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    PRESET_MODE_MANUAL,
    PROP_FAN_SPEED,
    PROP_MODE,
    PROP_POWER_FLAG,
)
from .coordinator import PhilipsAirplusDataCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Philips Air+ fan."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([PhilipsAirplusFan(coordinator, entry)])


class PhilipsAirplusFan(CoordinatorEntity, FanEntity):
    """Representation of a Philips Air+ fan."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED |
        FanEntityFeature.PRESET_MODE |
        FanEntityFeature.TURN_ON |
        FanEntityFeature.TURN_OFF
    )

    def __init__(
        self,
        coordinator: PhilipsAirplusDataCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the fan."""
        super().__init__(coordinator)
        self.entry = entry
        
        self._attr_unique_id = f"{entry.data['device_uuid']}_fan"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.data["device_uuid"])},
            "name": entry.data["device_name"],
            "manufacturer": "Philips",
            "model": self.coordinator._model_config.get("name", "Air+ Device"),
        }

    def _get_device_property(self, property_name: str) -> Any:
        """Get a property value from the device state using the model config mapping."""
        raw_key = self.coordinator._model_config.get("properties", {}).get(property_name)
        if not raw_key:
            return None
        return self.coordinator.device_state.get(raw_key)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.is_connected

    @property
    def is_on(self) -> bool:
        """Return True if the fan is on."""
        power = self._get_device_property(PROP_POWER_FLAG)
        if power is not None and int(power) == 0:
            return False
            
        speed = self._get_device_property(PROP_FAN_SPEED)
        if speed is None:
            return False
        return int(speed) > 0

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug("Coordinator update: %s", self.coordinator.data)
        self.async_write_ha_state()

    @property
    def current_speed(self) -> Optional[int]:
        """Return the current speed."""
        return self._get_device_property(PROP_FAN_SPEED)

    @property
    def speed_count(self) -> int:
        """Return the number of speeds the fan supports."""
        return 100

    @property
    def percentage(self) -> Optional[int]:
        """Return the current speed percentage."""
        speed = self._get_device_property(PROP_FAN_SPEED)
        if not self.is_on:
            return 0
            
        # Get supported speeds from model config (already ordered by intensity)
        supported_speeds = self.coordinator._model_config.get("speeds", [])
        if not supported_speeds:
            return 0
            
        try:
            speed_int = int(speed)
            if speed_int in supported_speeds:
                # Use speeds as-is (already ordered by intensity in models.yaml)
                idx = supported_speeds.index(speed_int)
                # Map index to percentage (1-based index / count * 100)
                return int(round((idx + 1) / len(supported_speeds) * 100))
        except (ValueError, TypeError):
            pass
            
        return 0

    @property
    def preset_mode(self) -> Optional[str]:
        """Return the current preset mode."""
        mode_value = self._get_device_property(PROP_MODE)
        if mode_value is None:
            return None
            
        name = self.coordinator._get_mode_name(mode_value)
        # Filter out manual mode if it's just a placeholder
        return name if name != PRESET_MODE_MANUAL else None

    @property
    def preset_modes(self) -> list[str]:
        """Return the list of available preset modes."""
        modes = self.coordinator._model_config.get("modes", {})
        return list(modes.keys())

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed percentage of the fan."""
        _LOGGER.debug("Setting percentage=%s", percentage)
        
        if percentage == 0:
            await self.async_turn_off()
            return
            
        supported_speeds = self.coordinator._model_config.get("speeds", [])
        if not supported_speeds:
            _LOGGER.error("No speeds defined for this model")
            return

        # Use speeds as-is (already ordered by intensity in models.yaml)
        # Map percentage to speed index: 1..100 -> 0..len-1
        idx = math.ceil(percentage / 100 * len(supported_speeds)) - 1
        idx = max(0, min(idx, len(supported_speeds) - 1))
        
        target_speed = supported_speeds[idx]
        _LOGGER.debug("Mapped percentage %s to speed value %s", percentage, target_speed)
        
        success = await self.coordinator.set_fan_speed(target_speed)
            
        if not success:
            _LOGGER.error("Failed to set speed to %s", target_speed)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode of the fan."""
        if preset_mode not in self.preset_modes:
            _LOGGER.error("Invalid preset mode: %s", preset_mode)
            return
            
        _LOGGER.debug("Setting preset mode to %s", preset_mode)
        success = await self.coordinator.set_mode(preset_mode)
        
        if not success:
            _LOGGER.error("Failed to set preset mode to %s", preset_mode)

    async def async_turn_on(self, *args: Any, **kwargs: Any) -> None:
        """Turn on the fan."""
        if "percentage" in kwargs:
            try:
                await self.coordinator.set_power(True)
            except Exception:
                _LOGGER.debug("Failed to call set_power before setting percentage")
            await self.async_set_percentage(kwargs["percentage"])
        else:
            try:
                await self.coordinator.set_power(True)
            except Exception:
                _LOGGER.debug("Failed to call set_power before turning on")

    async def async_turn_off(self, *args: Any, **kwargs: Any) -> None:
        """Turn off the fan."""
        _LOGGER.debug("Turning off fan")
        success = await self.coordinator.set_power(False)
        if not success:
            _LOGGER.error("Failed to turn off fan")

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        attributes = {}
        
        raw_mode = self._get_device_property(PROP_MODE)
        if raw_mode is not None:
            attributes["raw_mode"] = raw_mode
        
        raw_speed = self._get_device_property(PROP_FAN_SPEED)
        if raw_speed is not None:
            attributes["raw_speed"] = raw_speed
        
        attributes["connected"] = self.coordinator.is_connected
        
        return attributes