"""Sensor entities for Philips Air+ integration."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
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

from .const import (
    DOMAIN,
    PROP_FILTER_CLEAN_REMAINING,
    PROP_FILTER_REPLACE_REMAINING,
)
from .coordinator import PhilipsAirplusDataCoordinator

_LOGGER = logging.getLogger(__name__)

# Base sensors present on all models (filter diagnostics)
BASE_SENSOR_DESCRIPTIONS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="filter_replace_percentage",
        name="Filter Replace",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.POWER_FACTOR,
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:air-filter",
    ),
    SensorEntityDescription(
        key="filter_replace_hours_remaining",
        name="Filter Replace Hours Remaining",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:air-filter",
    ),
    SensorEntityDescription(
        key="filter_clean_percentage",
        name="Filter Clean",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.POWER_FACTOR,
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:air-filter",
    ),
    SensorEntityDescription(
        key="filter_clean_hours_remaining",
        name="Filter Clean Hours Remaining",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:air-filter",
    ),
]

# Model-specific sensor descriptions, keyed by the sensor key used in models.yaml
MODEL_SENSOR_DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    "pm25": SensorEntityDescription(
        key="pm25",
        name="PM2.5",
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        icon="mdi:air-filter",
    ),
    "allergen_index": SensorEntityDescription(
        key="allergen_index",
        name="Allergen Index",
        icon="mdi:flower-pollen",
    ),
    "diag_D0312C": SensorEntityDescription(
        key="diag_D0312C",
        name="Diagnostic D0312C",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:help-circle-outline",
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Philips Air+ sensors."""
    coordinator: PhilipsAirplusDataCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Always add base (filter) sensors immediately
    async_add_entities(
        [PhilipsAirplusSensor(coordinator, entry, d) for d in BASE_SENSOR_DESCRIPTIONS]
    )

    _model_sensors_added = False

    def _add_model_sensors() -> None:
        nonlocal _model_sensors_added
        if _model_sensors_added:
            return
        sensor_keys: list[str] = coordinator._model_config.get("sensors", [])
        if not sensor_keys:
            return
        _model_sensors_added = True
        entities = []
        for key in sensor_keys:
            if key in MODEL_SENSOR_DESCRIPTIONS:
                entities.append(
                    PhilipsAirplusSensor(coordinator, entry, MODEL_SENSOR_DESCRIPTIONS[key])
                )
            else:
                _LOGGER.warning("Model sensor key '%s' has no description, skipping", key)
        if entities:
            _LOGGER.debug(
                "Adding %d model-specific sensor(s) for %s",
                len(entities),
                coordinator._model_config.get("name"),
            )
            async_add_entities(entities)

    # Try immediately in case the model was already identified before this platform loaded
    _add_model_sensors()

    if not _model_sensors_added:
        # Register a coordinator listener that fires on every data update.
        # It will add model-specific sensors once the model config is known
        # and remove itself afterwards.
        def _on_coordinator_update() -> None:
            _add_model_sensors()

        unsub = coordinator.async_add_listener(_on_coordinator_update)
        entry.async_on_unload(unsub)


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

        # Use stable unique_id based on device UUID so entity registry matches
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
    def native_value(self) -> Optional[str | int | float]:
        """Return the native value of the sensor."""
        key = self.entity_description.key

        if key.startswith("filter_"):
            if self.coordinator.data:
                filter_info = self.coordinator.data.get("filter_info", {})
                return filter_info.get(key.replace("filter_", ""))

        # Model-specific sensor: look up raw property ID from model config
        if self.coordinator.data:
            device_state = self.coordinator.data.get("device_state", {})
            model_props = self.coordinator._model_config.get("properties", {})
            raw_id = model_props.get(key)
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
        attributes = {}

        if key.startswith("filter_") and self.coordinator.data:
            filter_info = self.coordinator.data.get("filter_info", {})
            if key == "filter_replace_percentage":
                if "replace_hours_total" in filter_info:
                    attributes["total_hours"] = filter_info["replace_hours_total"]
            elif key == "filter_clean_percentage":
                if "clean_hours_total" in filter_info:
                    attributes["total_hours"] = filter_info["clean_hours_total"]

        return attributes
