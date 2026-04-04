"""Switch entities for Philips Air+ integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PhilipsAirplusDataCoordinator

_LOGGER = logging.getLogger(__name__)

# Model-specific switch descriptions, keyed by the switch key used in models.yaml
MODEL_SWITCH_DESCRIPTIONS: dict[str, SwitchEntityDescription] = {
    "standby_monitor": SwitchEntityDescription(
        key="standby_monitor",
        name="Sensor Standby Monitor",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:eye-check-outline",
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Philips Air+ switches."""
    coordinator: PhilipsAirplusDataCoordinator = hass.data[DOMAIN][entry.entry_id]

    _switches_added = False

    def _add_model_switches() -> None:
        nonlocal _switches_added
        if _switches_added:
            return
        switch_keys: list[str] = coordinator._model_config.get("switches", [])
        if not switch_keys:
            return
        _switches_added = True
        entities = []
        for key in switch_keys:
            if key in MODEL_SWITCH_DESCRIPTIONS:
                entities.append(
                    PhilipsAirplusSwitch(coordinator, entry, MODEL_SWITCH_DESCRIPTIONS[key])
                )
            else:
                _LOGGER.warning("Model switch key '%s' has no description, skipping", key)
        if entities:
            _LOGGER.debug(
                "Adding %d model-specific switch(es) for %s",
                len(entities),
                coordinator._model_config.get("name"),
            )
            async_add_entities(entities)

    _add_model_switches()

    if not _switches_added:
        def _on_coordinator_update() -> None:
            _add_model_switches()

        unsub = coordinator.async_add_listener(_on_coordinator_update)
        entry.async_on_unload(unsub)


class PhilipsAirplusSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a Philips Air+ switch."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PhilipsAirplusDataCoordinator,
        entry: ConfigEntry,
        description: SwitchEntityDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self.entry = entry
        self.entity_description = description

        self._attr_unique_id = f"{entry.data['device_uuid']}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.data["device_uuid"])},
            "name": entry.data["device_name"],
            "manufacturer": "Philips",
            "model": self.coordinator._model_config.get("name", "Air+ Device"),
        }

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.is_connected

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        if not self.coordinator.data:
            return None
        device_state = self.coordinator.data.get("device_state", {})
        model_props = self.coordinator._model_config.get("properties", {})
        raw_id = model_props.get(self.entity_description.key)
        if raw_id is None:
            return None
        value = device_state.get(raw_id)
        if value is None:
            return None
        return bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self.coordinator.set_property(self.entity_description.key, 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self.coordinator.set_property(self.entity_description.key, 0)

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
