"""Fan entity for Philips Air+ integration."""
from __future__ import annotations

import logging
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
    """Set up Philips Air+ fan.

    The fan entity is registered lazily: we wait until the coordinator has
    identified the device model so that device_info contains the correct model
    name from the start.  Once registered the listener removes itself.
    """
    coordinator = hass.data[DOMAIN][entry.entry_id]

    _added = False
    unsub_ref: list = [None]

    def _try_add() -> None:
        nonlocal _added
        if _added or not coordinator._model_config.get("name"):
            return
        _added = True
        async_add_entities([PhilipsAirplusFan(coordinator, entry)])
        if unsub_ref[0]:
            unsub_ref[0]()
            unsub_ref[0] = None

    unsub_ref[0] = coordinator.async_add_listener(_try_add)
    entry.async_on_unload(lambda: unsub_ref[0]() if unsub_ref[0] else None)
    _try_add()  # Immediate attempt in case model is already known from cache


class PhilipsAirplusFan(CoordinatorEntity, FanEntity):
    """Representation of a Philips Air+ fan."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_force_update = True  # Force HA to record state updates even if unchanged
    _attr_supported_features = (
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
            "model": coordinator._model_config.get("name"),
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
        """Return True if the fan is on.

        D0310D is the power flag: 0 = off, any non-zero value = on.
        Observed values: 0 (off), 2 (on). Using != 0 to be robust
        against further undocumented values.
        """
        power = self._get_device_property(PROP_POWER_FLAG)
        if power is None:
            return False
        return int(power) != 0

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug("Coordinator update: %s", self.coordinator.data)
        self.async_write_ha_state()

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

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode of the fan."""
        if preset_mode not in self.preset_modes:
            _LOGGER.error("Invalid preset mode: %s", preset_mode)
            return
            
        _LOGGER.debug("Setting preset mode to %s", preset_mode)
        success = await self.coordinator.set_mode(preset_mode)
        
        if not success:
            _LOGGER.error("Failed to set preset mode to %s", preset_mode)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        await self.coordinator.set_power(True)
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)

    async def async_turn_off(self, **kwargs: Any) -> None:
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
        
        attributes["connected"] = self.coordinator.is_connected
        
        # Include last update timestamp to ensure HA updates "last updated" on each refresh
        # Use coordinator.data which is updated via async_set_updated_data()
        if self.coordinator.data:
            last_update = self.coordinator.data.get("last_update")
            if last_update:
                attributes["last_update"] = last_update.isoformat()
        
        return attributes