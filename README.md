# Philips Air+ Home Assistant Integration

Custom integration for Philips Air+ air purifiers. It communicates with the Philips/Versuni cloud service using the same MQTT protocol as the official mobile app.

This is a fork of [ShorMeneses/philips-airplus-homeassistant](https://github.com/ShorMeneses/philips-airplus-homeassistant), significantly extended and rearchitected: the original supported only AC0650/10 with a hardcoded device model; this fork introduces a model-agnostic, YAML-driven entity system and adds full AC0651/10 support.

## Features

- Fan control with preset modes and on/off
- Fan level sensor with history, useful in Auto mode to see current intensity
- Filter replacement and cleaning life sensors
- Maintenance resets via buttons or HA services
- Real-time updates via MQTT subscription
- Air quality sensors: PM2.5 concentration and allergen index
- Standby monitor support for AC0651/10

## Supported Devices

| Model | Modes | Fan Level | Filter Monitoring | Air Quality | Standby Monitor |
|-------|-------|-----------|-------------------|-------------|-----------------|
| AC0650/10 | Auto, Sleep, Turbo | Yes | Yes | PM2.5 | No |
| AC0651/10 | Auto, Medium, Sleep, Turbo | Yes | Yes | PM2.5, Allergen Index | Yes |

Other Air+ models sharing the same MQTT protocol may work but are untested. New models can be added via `models.yaml` without code changes.

Before installing this integration, check whether your device is already supported by [kongo09/philips-airpurifier-coap](https://github.com/kongo09/philips-airpurifier-coap). If it is listed there, you may be able to confirm the model and protocol details there first, which makes setup and troubleshooting easier.

Before installing this integration, check whether your device is already supported by [kongo09/philips-airpurifier-coap](https://github.com/kongo09/philips-airpurifier-coap). If it is listed there, you may be able to confirm the model and protocol details there first, which makes setup and troubleshooting easier.

## Installation

### via HACS (Recommended)

1. Go to HACS > Integrations
2. Click the three dots menu and select "Custom repositories"
3. Add repository: `https://github.com/ShorMeneses/philips-airplus-homeassistant`
4. Select "Integration" as category
5. Click "Add"
6. Go to HACS > Integrations and search for "Philips Air+"
7. Click "Install" and restart Home Assistant

### Manual Installation

1. Copy the `custom_components/philips_airplus` directory to your Home Assistant `config/custom_components` directory
2. Restart Home Assistant

## Configuration

### Prerequisites

A Philips Air+ account with your device already set up in the official mobile app.

### Authentication: OAuth PKCE Flow

1. Add the integration in Home Assistant. A login URL will be shown.
2. Open that URL in your browser.
3. Before logging in, open browser DevTools and switch to the Network tab.
4. Complete the login and authorization on the Philips website.
5. In the Network tab, find the redirect to `com.philips.air://loginredirect?code=...` and copy the full URL.
6. Paste it into Home Assistant as the Authorization Code.

Notes:

- On desktop browsers, the `com.philips.air://...` request will fail to open. This is expected.
- You can paste the full redirect URL or just the code value; the integration extracts the code automatically.
- If the token expires, go to Integration > Configure and paste a new authorization code. No need to remove and re-add the integration.

## Services

Two HA services are registered:

- `philips_airplus.reset_filter_clean` replicates the official app's "Filter cleaned" reset
- `philips_airplus.reset_filter_replace` replicates the official app's "New filter" reset

Both accept an optional `device_uuid` parameter to target a specific device when multiple are configured.

## Architecture

All device-specific behavior is driven by `models.yaml`. Each model entry declares its MQTT properties, preset modes, and which sensors, switches, and buttons to create. Adding support for a new model requires only a new entry in `models.yaml`.

Entities are registered lazily: the integration waits for the device to report its model identifier over MQTT before creating entities, so `device_info` can contain the correct model name from the start.
