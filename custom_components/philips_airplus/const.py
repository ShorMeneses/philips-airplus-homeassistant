"""Constants for Philips Air+ integration."""

from datetime import timedelta

DOMAIN = "philips_airplus"

# API endpoints
API_HOST = "prod.eu-da.iot.versuni.com"
API_BASE_URL = f"https://{API_HOST}/api"
DEVICE_ENDPOINT = f"{API_BASE_URL}/da/user/self/device"
SIGNATURE_ENDPOINT = f"{API_BASE_URL}/da/user/self/signature"
USER_SELF_ENDPOINT = f"{API_BASE_URL}/da/user/self"

# HTTP identity
# Keep a mobile-style user agent to better match official app traffic.
HTTP_USER_AGENT = "okhttp/4.12.0 (Android 14; Pixel 7)"

# Default OIDC settings (can be overridden by environment variables)
# Script example issuer path contains a tenant segment like 4_JGZWlP8eQHpEqkvQElolbA
OIDC_DEFAULT_ISSUER_BASE = "https://cdc.accounts.home.id/oidc/op/v1.0"
OIDC_DEFAULT_TENANT_SEGMENT = "4_JGZWlP8eQHpEqkvQElolbA"
OIDC_DEFAULT_REDIRECT_URI = "com.philips.air://loginredirect"
OIDC_DEFAULT_SCOPES = (
    "openid email profile address DI.Account.read DI.Account.write DI.AccountProfile.read "
    "DI.AccountProfile.write DI.AccountGeneralConsent.read DI.AccountGeneralConsent.write "
    "DI.GeneralConsent.read subscriptions profile_extended consents DI.AccountSubscription.read "
    "DI.AccountSubscription.write"
)

# MQTT configuration
MQTT_HOST = "ats.prod.eu-da.iot.versuni.com"
MQTT_PORT = 443
MQTT_PATH = "/mqtt"
KEEPALIVE = 4

# Authentication
AUTH_MODE_OAUTH = "oauth"
# Authentication
AUTH_MODE_OAUTH = "oauth"


# Fan speed ranges
FAN_SPEED_MIN = 1
FAN_SPEED_MAX = 18


PORT_FILTER_READ = "filtRd"
PORT_STATUS = "Status"
PORT_CONTROL = "Control"
PORT_CONFIG = "Config"

# Preset modes
PRESET_MODE_AUTO = "auto"
PRESET_MODE_SLEEP = "sleep"
PRESET_MODE_TURBO = "turbo"
PRESET_MODE_MANUAL = "manual"

# Properties
PROP_FAN_SPEED = "fan_speed"
PROP_MODE = "mode"
PROP_POWER_FLAG = "power"

PROP_FILTER_CLEAN_NOMINAL = "filter_clean_nominal"
PROP_FILTER_CLEAN_REMAINING = "filter_clean_remaining"
PROP_FILTER_REPLACE_NOMINAL = "filter_replace_nominal"
PROP_FILTER_REPLACE_REMAINING = "filter_replace_remaining"
PROP_SESSION_OWNER = "owner"


# MQTT topics
TOPIC_CONTROL_TEMPLATE = "da_ctrl/{device_id}/to_ncp"
TOPIC_STATUS_TEMPLATE = "da_ctrl/{device_id}/from_ncp"
TOPIC_SHADOW_UPDATE_TEMPLATE = "$aws/things/{device_id}/shadow/update"
TOPIC_SHADOW_GET_TEMPLATE = "$aws/things/{device_id}/shadow/get"

# Configuration keys
CONF_ACCESS_TOKEN = "access_token"
CONF_AUTH_MODE = "auth_mode"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"
CONF_DEVICE_UUID = "device_uuid"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_USER_ID = "user_id"
CONF_CLIENT_ID = "client_id"
CONF_TOKEN_EXPIRES_AT = "token_expires_at"
DEFAULT_CLIENT_ID = "-XsK7O6iEkLml77yDGDUi0ku"
# Integration-level enable/disable flag
CONF_ENABLE_MQTT = "enable_mqtt"

# Update intervals
# Default polling interval (was 30s). Increased to reduce network chatter.
SCAN_INTERVAL = timedelta(seconds=120)
TOKEN_REFRESH_BUFFER = timedelta(minutes=15)

# Error messages
ERROR_AUTH_FAILED = "Authentication failed"
ERROR_CONNECTION_FAILED = "Connection failed"
ERROR_DEVICE_NOT_FOUND = "Device not found"
ERROR_INVALID_TOKEN = "Invalid token"
ERROR_NETWORK_ERROR = "Network error"

# Component requirements
REQUIREMENTS = [
    "paho-mqtt>=1.6.0",
]

# Component version
__version__ = "0.2.0"
