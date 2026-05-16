"""MQTT client for Philips Air+ integration."""
from __future__ import annotations

import json
import logging
import random
import string
import time
import asyncio
import ssl
import threading
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import paho.mqtt.client as mqtt
from paho.mqtt.client import MQTTMessage

from .const import (
    KEEPALIVE,
    MQTT_HOST,
    MQTT_PATH,
    MQTT_PORT,
    PORT_CONTROL,
    PORT_FILTER_READ,
    PORT_FILTER_WRITE,
    PORT_STATUS,
    TOPIC_CONTROL_TEMPLATE,
    TOPIC_STATUS_TEMPLATE,
)

_LOGGER = logging.getLogger(__name__)


class PhilipsAirplusMQTTClient:
    """MQTT client for Philips Air+ communication."""

    def __init__(
        self,
        device_id: str,
        access_token: str,
        signature: str,
        client_id: Optional[str] = None,
        custom_authorizer_name: str = "CustomAuthorizer",
    ) -> None:
        """Initialize MQTT client."""
        self.device_id = device_id
        if not self.device_id.startswith('da-'):
            self.device_id = f"da-{self.device_id}"
        self.access_token = access_token
        self.signature = signature
        self.client_id = client_id or f"ha-{device_id}"
        self.custom_authorizer_name = custom_authorizer_name
        
        self._client: Optional[mqtt.Client] = None
        self._connected = False
        self._connecting = False
        self._last_disconnect_time: float = 0.0
        self._last_disconnect_rc: int = 0
        self._reconnect_attempts: int = 0
        self._reconnect_base: float = 1.0
        self._reconnect_max_backoff: float = 300.0
        self._rc7_cooldown: float = 120.0
        self._lock = threading.Lock()
        self._message_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._connection_callback: Optional[Callable[[bool], None]] = None
        self._refreshing_credentials: bool = False  # Flag to maintain availability during credential refresh
        
        self.outbound_topic = TOPIC_CONTROL_TEMPLATE.format(device_id=self.device_id)
        self.inbound_topic = TOPIC_STATUS_TEMPLATE.format(device_id=self.device_id)

        # Port names — defaults from const.py, overridable per model via configure_ports()
        self._port_status = PORT_STATUS
        self._port_control = PORT_CONTROL
        self._port_filter_read = PORT_FILTER_READ
        self._port_filter_write = PORT_FILTER_WRITE

    def configure_ports(self, ports: Dict[str, str]) -> None:
        """Update port names from model config."""
        self._port_status = ports.get("status", PORT_STATUS)
        self._port_control = ports.get("control", PORT_CONTROL)
        self._port_filter_read = ports.get("filter_read", PORT_FILTER_READ)
        self._port_filter_write = ports.get("filter_write", PORT_FILTER_WRITE)

    def set_message_callback(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Set callback for incoming messages."""
        self._message_callback = callback

    def set_connection_callback(self, callback: Callable[[bool], None]) -> None:
        """Set callback for connection status changes."""
        self._connection_callback = callback

    def _build_headers(self) -> Dict[str, str]:
        """Build WebSocket headers for authentication."""
        return {
            'x-amz-customauthorizer-name': self.custom_authorizer_name,
            'x-amz-customauthorizer-signature': self.signature,
            'tenant': 'da',
            'content-type': 'application/json',
            'token-header': f'Bearer {self.access_token.strip()}',
            'Sec-WebSocket-Protocol': 'mqtt',
        }

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Dict[str, Any], rc: int) -> None:
        """Handle MQTT connection."""
        _LOGGER.info("Connected to MQTT with rc=%s", rc)
        
        if rc == 0:
            self._connected = True
            self._reconnect_attempts = 0
            self._last_disconnect_rc = 0
            self._last_disconnect_time = 0.0
            client.subscribe(self.inbound_topic, qos=0)
            _LOGGER.info("Subscribed to %s", self.inbound_topic)
            
            if self._connection_callback:
                self._connection_callback(True)
        else:
            self._connected = False
            _LOGGER.error("Connection failed with rc=%s", rc)
            
            if self._connection_callback:
                self._connection_callback(False)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: MQTTMessage) -> None:
        """Handle incoming MQTT messages."""
        try:
            payload = msg.payload.decode('utf-8')
            message_data = json.loads(payload)
            
            _LOGGER.debug("Received message: %s", message_data)
            
            if self._message_callback:
                self._message_callback(message_data)
                
        except json.JSONDecodeError as ex:
            _LOGGER.error("Failed to decode MQTT message: %s", ex)
        except Exception as ex:
            _LOGGER.error("Error processing MQTT message: %s", ex)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, rc: int) -> None:
        """Handle MQTT disconnect events."""
        _LOGGER.debug("Disconnected from MQTT with rc=%s", rc)
        if rc != 0:
            _LOGGER.warning("MQTT unexpected disconnect rc=%s", rc)
            try:
                self._reconnect_attempts = min(self._reconnect_attempts + 1, 32)
            except Exception:
                self._reconnect_attempts = 1
            self._last_disconnect_time = time.time()
            self._last_disconnect_rc = rc
            try:
                client.loop_stop()
            except Exception:
                pass
            try:
                client.disconnect()
            except Exception:
                pass
            self._client = None
        self._connected = False
        
        # Skip connection callback during credential refresh to prevent unavailable state
        if self._connection_callback and not self._refreshing_credentials:
            self._connection_callback(False)

    def _blocking_connect(self, timeout: float = 15.0) -> bool:
        """Connect to MQTT broker."""
        with self._lock:
            if self._connecting:
                return False
            if self._connected:
                return True
            self._connecting = True
        
        try:
            # Apply backoff if recent disconnect
            if self._last_disconnect_time and self._last_disconnect_rc != 0:
                elapsed = time.time() - self._last_disconnect_time
                backoff = min(self._reconnect_base * (2 ** max(0, self._reconnect_attempts - 1)), self._reconnect_max_backoff)
                if elapsed < backoff:
                    wait = backoff - elapsed
                    _LOGGER.warning("Throttling reconnect for %.1fs", wait)
                    time.sleep(wait)
            
            # Special cooldown for rc=7
            if self._last_disconnect_rc == 7:
                elapsed_since = time.time() - self._last_disconnect_time if self._last_disconnect_time else None
                if elapsed_since is not None and elapsed_since < self._rc7_cooldown:
                    wait = self._rc7_cooldown - elapsed_since
                    _LOGGER.warning("Recent rc=7 disconnect; enforcing cooldown for %.1fs", wait)
                    time.sleep(wait)
            
            headers = self._build_headers()
            
            self._client = mqtt.Client(
                client_id=self.client_id,
                transport='websockets',
                protocol=mqtt.MQTTv311
            )
            
            self._client.ws_set_options(path=MQTT_PATH, headers=headers)
            
            try:
                self._client.tls_set(tls_version=ssl.PROTOCOL_TLSv1_2)
            except Exception as tls_ex:
                _LOGGER.warning("Failed to set TLSv1.2: %s", tls_ex)
            
            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message
            self._client.on_disconnect = self._on_disconnect
            
            _LOGGER.debug("Connecting to %s:%s with client_id=%s", MQTT_HOST, MQTT_PORT, self.client_id)
            self._client.connect(MQTT_HOST, MQTT_PORT, keepalive=KEEPALIVE)
            self._client.loop_start()
            
            start_ts = time.time()
            while (time.time() - start_ts) < timeout:
                if self._connected:
                    break
                time.sleep(0.1)
            
            if not self._connected:
                _LOGGER.error("Connection timeout after %.2fs", time.time() - start_ts)
                if self._client:
                    try:
                        self._client.loop_stop()
                    except Exception:
                        pass
                    try:
                        self._client.disconnect()
                    except Exception:
                        pass
                self._client = None
                return False
            
            _LOGGER.info("MQTT connected successfully (%.2fs)", time.time() - start_ts)
            return True
            
        except Exception as ex:
            _LOGGER.error("Failed during MQTT connect: %s", ex)
            if self._client:
                try:
                    self._client.loop_stop()
                except Exception:
                    pass
                try:
                    self._client.disconnect()
                except Exception:
                    pass
            self._client = None
            return False
        finally:
            with self._lock:
                self._connecting = False

    async def async_connect(self) -> bool:
        """Async wrapper for MQTT connect."""
        if self._connected:
            return True
        if self._connecting:
            return False
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._blocking_connect)

    def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        with self._lock:
            if self._client:
                try:
                    self._client.loop_stop()
                    self._client.disconnect()
                    _LOGGER.debug("MQTT disconnected")
                except Exception as ex:
                    _LOGGER.error("Error disconnecting MQTT: %s", ex)
                finally:
                    self._client = None
            self._connected = False

    def is_connected(self) -> bool:
        """Check if MQTT client is connected.
        
        Returns True during credential refresh to prevent unavailable state
        while reconnecting with new tokens.
        """
        return self._connected or self._refreshing_credentials

    def _generate_correlation_id(self) -> str:
        """Generate a correlation ID for commands."""
        return ''.join(random.choices(string.hexdigits.lower(), k=8))

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'

    def _build_command_payload(
        self,
        command_name: str,
        port_name: str,
        properties: Dict[str, Any]
    ) -> str:
        """Build command payload."""
        payload = {
            'cid': self._generate_correlation_id(),
            'time': self._get_timestamp(),
            'type': 'command',
            'cn': command_name,
            'ct': 'mobile',
            'data': {
                'portName': port_name,
                'properties': properties
            }
        }
        return json.dumps(payload, separators=(',', ':'))

    def set_mode(self, mode: int, raw_key: str) -> bool:
        """Set device mode using raw property key."""
        if not self._connected:
            _LOGGER.error("MQTT not connected")
            return False
        
        payload = self._build_command_payload(
            'setPort',
            self._port_control,
            {raw_key: mode}
        )
        
        _LOGGER.debug("Setting mode to %s using key %s", mode, raw_key)
        success = self._publish(payload)
        
        try:
            self.request_port_status(self._port_status)
        except Exception:
            pass
        
        return success

    def set_power(self, power_on: bool) -> bool:
        """Set power state via AWS IoT shadow update."""
        if not self._connected:
            _LOGGER.error("MQTT not connected")
            return False

        desired = {"state": {"desired": {"powerOn": power_on}}}
        shadow_payload = json.dumps(desired, separators=(',', ':'))
        success = self._publish(shadow_payload, topic=f"$aws/things/{self.device_id}/shadow/update")

        if success:
            self.request_port_status(self._port_status)

        return success

    def set_property(self, raw_key: str, value: Any) -> bool:
        """Set a single device property via the Control port."""
        if not self._connected:
            _LOGGER.error("MQTT not connected")
            return False

        payload = self._build_command_payload(
            'setPort',
            self._port_control,
            {raw_key: value},
        )

        _LOGGER.debug("Setting property %s to %s", raw_key, value)
        res = self._publish(payload)

        try:
            self.request_port_status(self._port_status)
        except Exception:
            pass

        return res

    def reset_filter_clean(self, raw_key: str, reset_value: int) -> bool:
        """Reset clean-filter maintenance timer.

        raw_key and reset_value come from the model config (filter_clean_reset_raw /
        filter_clean_reset_value), matching the official app's MQTT publish pattern.
        """
        if not self._connected:
            _LOGGER.error("MQTT not connected")
            return False

        payload = self._build_command_payload('setPort', self._port_filter_write, {raw_key: reset_value})
        _LOGGER.debug("Resetting clean-filter timer: %s = %s", raw_key, reset_value)
        success = self._publish(payload, qos=1)

        try:
            self.request_port_status(self._port_filter_read)
        except Exception:
            pass

        return success

    def reset_filter_replace(self, raw_key: str, reset_value: int) -> bool:
        """Reset replace-filter maintenance timer.

        raw_key and reset_value come from the model config (filter_replace_reset_raw /
        filter_replace_reset_value), matching the official app's MQTT publish pattern.
        """
        if not self._connected:
            _LOGGER.error("MQTT not connected")
            return False

        payload = self._build_command_payload('setPort', self._port_filter_write, {raw_key: reset_value})
        _LOGGER.debug("Resetting replace-filter timer: %s = %s", raw_key, reset_value)
        success = self._publish(payload, qos=1)

        try:
            self.request_port_status(self._port_filter_read)
        except Exception:
            pass

        return success

    def request_port_status(self, port_name: str) -> bool:
        """Request status for a specific port."""
        if not self._connected:
            _LOGGER.error("MQTT not connected")
            return False

        payload = self._build_command_payload(
            'getPort',
            port_name,
            {}
        )
        
        _LOGGER.debug("Requesting status for port %s", port_name)
        return self._publish(payload)

    def request_shadow_get(self) -> bool:
        """Request AWS IoT shadow get."""
        if not self._connected:
            _LOGGER.error("MQTT not connected")
            return False

        shadow_topic = f"$aws/things/{self.device_id}/shadow/get"
        _LOGGER.debug("Requesting shadow get")
        return self._publish('{}', topic=shadow_topic)

    def _publish(self, payload: str, topic: Optional[str] = None, qos: int = 0) -> bool:
        """Publish message to MQTT broker."""
        if not self._client or not self._connected:
            _LOGGER.error("MQTT client not connected")
            return False

        try:
            publish_topic = topic or self.outbound_topic
            result = self._client.publish(publish_topic, payload, qos=qos)
            
            if getattr(result, 'rc', None) == mqtt.MQTT_ERR_SUCCESS:
                _LOGGER.debug("Published to %s: %s", publish_topic, payload)
                return True
            else:
                _LOGGER.error("Failed to publish to %s: rc=%s", publish_topic, getattr(result, 'rc', None))
                return False
                
        except Exception as ex:
            _LOGGER.error("Error publishing message: %s", ex)
            return False

    async def async_update_credentials(self, access_token: str, signature: str) -> bool:
        """Update credentials and reconnect.
        
        Sets _refreshing_credentials flag to maintain availability during reconnection.
        """
        self.access_token = access_token
        self.signature = signature
        
        with self._lock:
            if self._connecting:
                _LOGGER.debug("Connect in progress; deferring credential update")
                return False
        
        # Set flag to prevent unavailable state during reconnection
        self._refreshing_credentials = True
        try:
            self.disconnect()
            await asyncio.sleep(1)  # Allow time for socket cleanup
            result = await self.async_connect()
            return result
        finally:
            self._refreshing_credentials = False
