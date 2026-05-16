"""Sensor entities for Philips Air+ integration."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    PERCENTAGE,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PhilipsAirplusDataCoordinator

_LOGGER = logging.getLogger(__name__)

# All sensor descriptions, keyed by the sensor key used in models.yaml.
# models.yaml is the single source of truth for which sensors a model exposes.
ALL_SENSOR_DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    "filter_replace_percentage": SensorEntityDescription(
        key="filter_replace_percentage",
        translation_key="filter_replace_percentage",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.POWER_FACTOR,
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:air-filter",
    ),
    "filter_replace_hours_remaining": SensorEntityDescription(
        key="filter_replace_hours_remaining",
        translation_key="filter_replace_hours_remaining",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:air-filter",
    ),
    "filter_clean_percentage": SensorEntityDescription(
        key="filter_clean_percentage",
        translation_key="filter_clean_percentage",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.POWER_FACTOR,
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:air-filter",
    ),
    "filter_clean_hours_remaining": SensorEntityDescription(
        key="filter_clean_hours_remaining",
        translation_key="filter_clean_hours_remaining",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:air-filter",
    ),
    "pm25": SensorEntityDescription(
        key="pm25",
        translation_key="pm25",
        device_class=SensorDeviceClass.PM25,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        icon="mdi:air-filter",
    ),
    "allergen_index": SensorEntityDescription(
        key="allergen_index",
        translation_key="allergen_index",
        icon="mdi:flower-pollen",
    ),
    "fan_level": SensorEntityDescription(
        key="fan_level",
        translation_key="fan_level",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:fan",
    ),
    "diag_D0312C": SensorEntityDescription(
        key="diag_D0312C",
        translation_key="diag_d0312c",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:help-circle-outline",
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Philips Air+ sensors.

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

        sensor_keys: list[str] = coordinator._model_config.get("sensors", [])
        entities = [
            PhilipsAirplusSensor(coordinator, entry, ALL_SENSOR_DESCRIPTIONS[key])
            for key in sensor_keys
            if key in ALL_SENSOR_DESCRIPTIONS
        ]
        for key in sensor_keys:
            if key not in ALL_SENSOR_DESCRIPTIONS:
                _LOGGER.warning("Sensor key '%s' in models.yaml has no description, skipping", key)

        _added_for = model_name
        if entities:
            _LOGGER.debug("Adding %d sensor(s) for model %s", len(entities), model_name)
            async_add_entities(entities)

        # Unsubscribe — model is identified, listener is no longer needed
        if unsub_ref[0]:
            unsub_ref[0]()
            unsub_ref[0] = None

    unsub_ref[0] = coordinator.async_add_listener(_try_add)
    entry.async_on_unload(lambda: unsub_ref[0]() if unsub_ref[0] else None)
    _try_add()  # Immediate attempt in case model is already known from cache


class PhilipsAirplusSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Philips Air+ sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PhilipsAirplusDataCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
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
    def native_value(self) -> Optional[str | int | float]:
        """Return the native value of the sensor."""
        if not self.coordinator.data:
            return None

        key = self.entity_description.key

        # Filter sensors: value comes from computed filter_info dict
        if key.startswith("filter_"):
            filter_info = self.coordinator.data.get("filter_info", {})
            return filter_info.get(key.replace("filter_", "", 1))

        # All other sensors: look up raw property ID from model config
        device_state = self.coordinator.data.get("device_state", {})
        raw_id = self.coordinator._model_config.get("properties", {}).get(key)
        if raw_id is not None:
            return device_state.get(raw_id)

        return None

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        key = self.entity_description.key
        if not key.startswith("filter_") or not self.coordinator.data:
            return {}

        filter_info = self.coordinator.data.get("filter_info", {})
        if key == "filter_replace_percentage" and "replace_hours_total" in filter_info:
            return {"total_hours": filter_info["replace_hours_total"]}
        if key == "filter_clean_percentage" and "clean_hours_total" in filter_info:
            return {"total_hours": filter_info["clean_hours_total"]}
        return {}
