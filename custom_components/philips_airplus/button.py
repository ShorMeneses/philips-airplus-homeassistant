"""Button entities for Philips Air+ integration."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PhilipsAirplusDataCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Philips Air+ buttons."""
    coordinator: PhilipsAirplusDataCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            PhilipsAirplusResetFilterCleanButton(coordinator, entry),
            PhilipsAirplusResetFilterReplaceButton(coordinator, entry),
        ]
    )


class _PhilipsAirplusBaseButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: PhilipsAirplusDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.data["device_uuid"])},
            "name": entry.data["device_name"],
            "manufacturer": "Philips",
            "model": self.coordinator._model_config.get("name", "Air+ Device"),
        }

    @property
    def available(self) -> bool:
        return self.coordinator.is_connected


class PhilipsAirplusResetFilterCleanButton(_PhilipsAirplusBaseButton):
    """Button: reset clean-filter maintenance timer."""

    _attr_icon = "mdi:air-filter"
    _attr_name = "Reset clean filter timer"

    def __init__(self, coordinator: PhilipsAirplusDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['device_uuid']}_reset_filter_clean"

    async def async_press(self) -> None:
        ok = await self.coordinator.reset_filter_clean()
        if not ok:
            _LOGGER.warning("Failed to reset clean filter timer for %s", self.entry.data.get("device_name"))


class PhilipsAirplusResetFilterReplaceButton(_PhilipsAirplusBaseButton):
    """Button: reset replace-filter maintenance timer."""

    _attr_icon = "mdi:air-filter"
    _attr_name = "Reset replace filter timer"

    def __init__(self, coordinator: PhilipsAirplusDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['device_uuid']}_reset_filter_replace"

    async def async_press(self) -> None:
        ok = await self.coordinator.reset_filter_replace()
        if not ok:
            _LOGGER.warning("Failed to reset replace filter timer for %s", self.entry.data.get("device_name"))
