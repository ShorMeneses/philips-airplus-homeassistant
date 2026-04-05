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

# All switch descriptions, keyed by the switch key used in models.yaml.
# models.yaml is the single source of truth for which switches a model exposes.
ALL_SWITCH_DESCRIPTIONS: dict[str, SwitchEntityDescription] = {
    "standby_monitor": SwitchEntityDescription(
        key="standby_monitor",
        translation_key="standby_monitor",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:eye-check-outline",
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Philips Air+ switches.

    Entities are registered lazily: we wait until the coordinator has identified
    the device model (via MQTT Config port or restored from cache).  Once the
    model is known the listener removes itself — it will never fire again.
    """
    coordinator: PhilipsAirplusDataCoordinator = hass.data[DOMAIN][entry.entry_id]

    _added_for: str | None = None
    unsub_ref: list = [None]

    def _try_add() -> None:
        nonlocal _added_for
        model_name = coordinator._model_config.get("name")
        if not model_name or _added_for == model_name:
            return

        switch_keys: list[str] = coordinator._model_config.get("switches", [])
        entities = [
            PhilipsAirplusSwitch(coordinator, entry, ALL_SWITCH_DESCRIPTIONS[key])
            for key in switch_keys
            if key in ALL_SWITCH_DESCRIPTIONS
        ]
        for key in switch_keys:
            if key not in ALL_SWITCH_DESCRIPTIONS:
                _LOGGER.warning("Switch key '%s' in models.yaml has no description, skipping", key)

        _added_for = model_name
        if entities:
            _LOGGER.debug("Adding %d switch(es) for model %s", len(entities), model_name)
            async_add_entities(entities)

        # Unsubscribe — model is identified, listener is no longer needed
        if unsub_ref[0]:
            unsub_ref[0]()
            unsub_ref[0] = None

    unsub_ref[0] = coordinator.async_add_listener(_try_add)
    entry.async_on_unload(lambda: unsub_ref[0]() if unsub_ref[0] else None)
    _try_add()  # Immediate attempt in case model is already known from cache


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
            "model": coordinator._model_config.get("name"),
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
        raw_id = self.coordinator._model_config.get("properties", {}).get(self.entity_description.key)
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
