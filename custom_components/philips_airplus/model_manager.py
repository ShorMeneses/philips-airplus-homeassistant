"""Model manager for Philips Air+ integration."""
from __future__ import annotations

import logging
import os
import yaml
from typing import Any, Dict, Optional

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class PhilipsAirplusModelManager:
    """Manager for device models."""

    def __init__(self, component_path: str) -> None:
        """Initialize the model manager."""
        self._models: Dict[str, Any] = {}
        self._default_model: Optional[str] = None
        self._load_models(component_path)

    def _load_models(self, component_path: str) -> None:
        """Load models from yaml file."""
        yaml_path = os.path.join(component_path, "models.yaml")
        try:
            # Note: This uses blocking I/O but it's acceptable for one-time initialization
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                self._models = data.get("models", {})
                self._default_model = data.get("default")
                _LOGGER.debug("Loaded %d models from %s", len(self._models), yaml_path)
        except Exception as ex:
            _LOGGER.error("Failed to load models.yaml: %s", ex)

    def get_model_config(self, model_id: str) -> Dict[str, Any]:
        """Get configuration for a specific model."""
        # Try exact match
        if model_id in self._models:
            return self._models[model_id]
        
        # Try partial match (e.g. AC0650)
        for key, config in self._models.items():
            if key in model_id:
                return config
                
        # Fallback to default
        if self._default_model and self._default_model in self._models:
            _LOGGER.warning("Model %s not found, using default %s", model_id, self._default_model)
            return self._models[self._default_model]
            
        _LOGGER.error("Model %s not found and no default available", model_id)
        return {}

    def get_mode_value(self, model_id: str, mode_name: str) -> Optional[int]:
        """Get value for a specific mode."""
        config = self.get_model_config(model_id)
        return config.get("modes", {}).get(mode_name)

    def get_mode_name(self, model_id: str, mode_value: int) -> Optional[str]:
        """Get name for a specific mode value."""
        config = self.get_model_config(model_id)
        for name, val in config.get("modes", {}).items():
            if val == mode_value:
                return name
        return None
