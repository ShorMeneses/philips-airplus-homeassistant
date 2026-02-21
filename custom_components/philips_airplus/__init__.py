"""Philips Air+ integration for Home Assistant."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, CONF_ENABLE_MQTT
from . import config_flow  # needed so HA can build the options flow
from .coordinator import PhilipsAirplusDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.FAN,
    Platform.SENSOR,
    Platform.BUTTON,
]


SERVICE_RESET_FILTER_CLEAN = "reset_filter_clean"
SERVICE_RESET_FILTER_REPLACE = "reset_filter_replace"

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("device_uuid"): cv.string,
    }
)


def _normalize_device_uuid(device_uuid: str) -> str:
    device_uuid = (device_uuid or "").strip()
    if device_uuid.startswith("da-"):
        return device_uuid[3:]
    return device_uuid


def _iter_coordinators(hass: HomeAssistant) -> list[PhilipsAirplusDataCoordinator]:
    domain_data = hass.data.get(DOMAIN, {})
    coordinators: list[PhilipsAirplusDataCoordinator] = []
    for key, value in domain_data.items():
        if key == "_services_registered":
            continue
        if isinstance(value, PhilipsAirplusDataCoordinator):
            coordinators.append(value)
    return coordinators


async def _handle_reset_service(call: ServiceCall, service_name: str) -> None:
    hass = call.hass
    target_uuid = call.data.get("device_uuid")
    target_uuid = _normalize_device_uuid(target_uuid) if target_uuid else None

    coordinators = _iter_coordinators(hass)
    if target_uuid:
        coordinators = [c for c in coordinators if c.device_uuid == target_uuid]

    if not coordinators:
        _LOGGER.info("Service %s: no matching devices (device_uuid=%s)", service_name, target_uuid)
        return

    for coordinator in coordinators:
        try:
            if service_name == SERVICE_RESET_FILTER_CLEAN:
                ok = await coordinator.reset_filter_clean()
            else:
                ok = await coordinator.reset_filter_replace()

            if ok:
                _LOGGER.info("Service %s succeeded for %s (%s)", service_name, coordinator.device_name, coordinator.device_uuid)
            else:
                _LOGGER.warning("Service %s failed for %s (%s)", service_name, coordinator.device_name, coordinator.device_uuid)
        except Exception as exc:
            _LOGGER.exception(
                "Service %s errored for %s (%s): %s",
                service_name,
                coordinator.device_name,
                coordinator.device_uuid,
                exc,
            )


def _ensure_services_registered(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("_services_registered"):
        return

    async def _service_reset_filter_clean(call: ServiceCall) -> None:
        await _handle_reset_service(call, SERVICE_RESET_FILTER_CLEAN)

    async def _service_reset_filter_replace(call: ServiceCall) -> None:
        await _handle_reset_service(call, SERVICE_RESET_FILTER_REPLACE)

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_FILTER_CLEAN,
        _service_reset_filter_clean,
        schema=SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_FILTER_REPLACE,
        _service_reset_filter_replace,
        schema=SERVICE_SCHEMA,
    )
    domain_data["_services_registered"] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Philips Air+ from a config entry."""
    # If all entities for this config entry are disabled in the entity registry,
    # skip active network setup to avoid unwanted MQTT connects. This respects
    # the user's decision to disable devices without removing the integration.
    try:
        registry = er.async_get(hass)
        entries = er.async_entries_for_config_entry(registry, entry.entry_id)
        # Integration-level option to disable MQTT entirely
        enable_mqtt = entry.options.get(CONF_ENABLE_MQTT, True)
        if not enable_mqtt:
            _LOGGER = __import__('logging').getLogger(__name__)
            _LOGGER.info("Config entry %s: enable_mqtt is False; skipping MQTT setup.", entry.entry_id)
            return True
        if entries:
            # If every entry is disabled by the user, skip setup
            # Some HA versions do not export DISABLED_USER; compare against literal 'user'
            all_disabled = all((e.disabled_by is not None and str(e.disabled_by).lower() == 'user') for e in entries)
            if all_disabled:
                _LOGGER = __import__('logging').getLogger(__name__)
                _LOGGER.info("All entities for config_entry %s are disabled by user; skipping setup.", entry.entry_id)
                return True
        else:
            # No entity entries yet; initial setup or entities removed — proceed only if enable_mqtt True
            _LOGGER = __import__('logging').getLogger(__name__)
            _LOGGER.debug("No registered entities for config_entry %s; proceeding (enable_mqtt=%s).", entry.entry_id, enable_mqtt)
    except Exception as exc:
        _LOGGER = __import__('logging').getLogger(__name__)
        _LOGGER.debug("Entity registry check failed: %s; proceeding with setup.", exc)

    coordinator = PhilipsAirplusDataCoordinator(hass, entry)
    
    try:
        await coordinator.async_setup()
        await coordinator.async_config_entry_first_refresh()
    except Exception as ex:
        raise ConfigEntryNotReady(f"Unable to connect to Philips Air+ device: {ex}") from ex
    
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Register domain services once (not per entry)
    _ensure_services_registered(hass)
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        await coordinator.async_shutdown()
    
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def async_get_options_flow(config_entry: ConfigEntry):
    """Return the options flow handler to expose Options in UI."""
    return config_flow.PhilipsAirplusOptionsFlowHandler(config_entry)