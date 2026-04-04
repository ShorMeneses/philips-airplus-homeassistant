"""Sensor entities for Philips Air+ integration."""
from __future__ import annotations

import logging
from typing import Optional

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

# Sensor descriptions
# Direct raw-ID mapping for device_state sensors (bypasses model-config timing)
DEVICE_STATE_PROPERTY_MAP: dict[str, str] = {
    "pm25":           "D03221",
    "allergen_index": "D03120",
    "standby_monitor": "D03134",
    "diag_D0312C":    "D0312C",
}

SENSOR_DESCRIPTIONS: list[SensorEntityDescription] = [
    # Filter sensors
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
    # Air quality sensors (AC0651/10 and compatible models)
    SensorEntityDescription(
        key="pm25",
        name="PM2.5",
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        icon="mdi:air-filter",
    ),
    SensorEntityDescription(
        key="allergen_index",
        name="Allergen Index",
        icon="mdi:flower-pollen",
    ),
    SensorEntityDescription(
        key="standby_monitor",
        name="Sensor Standby Monitor",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:eye-check-outline",
    ),
    SensorEntityDescription(
        key="diag_D0312C",
        name="Diagnostic D0312C",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:help-circle-outline",
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Philips Air+ sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    entities = []
    for description in SENSOR_DESCRIPTIONS:
        entities.append(PhilipsAirplusSensor(coordinator, entry, description))
    
    async_add_entities(entities)


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

        # Direct device_state lookup (raw ID known at sensor level)
        if key in DEVICE_STATE_PROPERTY_MAP and self.coordinator.data:
            device_state = self.coordinator.data.get("device_state", {})
            raw_id = DEVICE_STATE_PROPERTY_MAP[key]
            if raw_id in device_state:
                return device_state[raw_id]

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
            # Add filter hours total if available (use calculated filter_info from coordinator data)
            filter_info = self.coordinator.data.get("filter_info", {})
            if key == "filter_replace_percentage":
                if "replace_hours_total" in filter_info:
                    attributes["total_hours"] = filter_info["replace_hours_total"]
            elif key == "filter_clean_percentage":
                if "clean_hours_total" in filter_info:
                    attributes["total_hours"] = filter_info["clean_hours_total"]
        
        return attributes