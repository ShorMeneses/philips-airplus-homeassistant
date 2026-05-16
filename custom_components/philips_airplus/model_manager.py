"""Model manager for Philips Air+ integration."""
from __future__ import annotations

import logging
import os
import yaml
from typing import Any, Dict

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class PhilipsAirplusModelManager:
    """Manager for device models."""

    def __init__(self, hass: HomeAssistant, component_path: str) -> None:
        """Initialize the model manager."""
        self._hass = hass
        self._component_path = component_path
        self._models: Dict[str, Any] = {}

    async def async_load_models(self) -> None:
        """Load models from yaml file asynchronously."""
        yaml_path = os.path.join(self._component_path, "models.yaml")

        def _load_yaml():
            """Load YAML file in executor."""
            with open(yaml_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)

        try:
            # Run blocking file I/O in executor to avoid blocking event loop
            data = await self._hass.async_add_executor_job(_load_yaml)
            self._models = data.get("models", {})
            _LOGGER.debug("Loaded %d models from %s", len(self._models), yaml_path)
        except Exception as ex:
            _LOGGER.error("Failed to load models.yaml: %s", ex)

    def get_model_config(self, model_id: str) -> Dict[str, Any]:
        """Get configuration for a specific model.

        Returns an empty dict if the model is not found — callers must handle
        the no-model case explicitly rather than relying on a silent fallback.
        """
        # Try exact match
        if model_id in self._models:
            return self._models[model_id]

        # Try prefix match (e.g. device reports "AC0650/10-EU", key is "AC0650/10")
        for key, config in self._models.items():
            if model_id.startswith(key):
                return config

        _LOGGER.warning("Model '%s' not found in models.yaml", model_id)
        return {}
