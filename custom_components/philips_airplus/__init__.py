"""Philips Air+ integration for Home Assistant."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, CONF_ENABLE_MQTT
from . import config_flow  # needed so HA can build the options flow
from .coordinator import PhilipsAirplusDataCoordinator

PLATFORMS: list[Platform] = [
    Platform.FAN,
    Platform.SENSOR,
]


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