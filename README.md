# Philips Air+ Home Assistant Integration

Custom integration for Philips Air+ air purifiers. It communicates with the Philips/Versuni cloud service using the same MQTT protocol as the official mobile app.

This version keeps the original `philips_airplus` Home Assistant domain while adding a YAML-driven model system, AC0651/10 support, extra sensors, service-based filter resets, and improved MQTT reconnect handling.

## Features

- Fan control with preset modes and on/off
- Fan level sensor with history, useful in Auto mode to see current intensity
- Filter replacement and cleaning life sensors
- Filter maintenance resets via buttons or Home Assistant services
- Live device updates through MQTT
- MQTT reconnect retry loop with token refresh before reconnecting
- PM2.5 concentration sensor
- Allergen index and standby monitor support for AC0651/10

## Supported Devices

| Model | Modes | Fan Level | Filter Monitoring | Air Quality | Standby Monitor |
|-------|-------|-----------|-------------------|-------------|-----------------|
| AC0650/10 | Auto, Sleep, Turbo | Yes | Yes | PM2.5 | No |
| AC0651/10 | Auto, Medium, Sleep, Turbo | Yes | Yes | PM2.5, Allergen Index | Yes |

Other Air+ models that share the same MQTT protocol may work but are untested. New models can be added via `models.yaml` without code changes.

## Installation

### via HACS

1. Go to HACS > Integrations.
2. Open the three-dot menu and select "Custom repositories".
3. Add repository: `https://github.com/ShorMeneses/philips-airplus-homeassistant`.
4. Select "Integration" as the category.
5. Click "Add".
6. Go to HACS > Integrations and search for "Philips Air+".
7. Install it and restart Home Assistant.

### Manual Installation

1. Copy the `custom_components/philips_airplus` directory to your Home Assistant `config/custom_components` directory.
2. Restart Home Assistant.

## Configuration

### Prerequisites

A Philips Air+ account with your device already set up in the official mobile app.

### Authentication: OAuth PKCE Flow

1. Add the integration in Home Assistant. A login URL will be shown.
2. Open that URL in your browser.
3. Before logging in, open browser DevTools and switch to the Network tab.
4. Complete login and authorization on the Philips website.
5. In Network requests, find the redirect request that looks like `com.philips.air://loginredirect?code=st2.xxxxxxx.sc3&state=xxxx`. It has to be this exact URL; there may be other URLs with similar values that will not work.
6. Copy only the `code` value, the part between `code=` and `&state`. In this example, copy only `st2.xxxxxxx.sc3`.
7. Paste that value into Home Assistant as the Authorization Code.

Notes:

- On desktop browsers, the `com.philips.air://...` request may fail to open because there is no app handler. This is expected; you only need the URL from Network.
- You can also paste the full redirect URL; the integration will extract the `code` value automatically.
- If the token expires later, open Integration > Configure and paste a new authorization code in the optional re-auth field. You do not need to remove and re-add the integration.
- Some browsers may not show the correct request. Microsoft Edge has been reported to work reliably.

## Services

Two Home Assistant services are registered:

- `philips_airplus.reset_filter_clean` replicates the official app's "Filter cleaned" reset.
- `philips_airplus.reset_filter_replace` replicates the official app's "New filter" reset.

Both accept an optional `device_uuid` parameter to target a specific device when multiple are configured.

## Architecture

Device-specific behavior is driven by `models.yaml`. Each model declares its MQTT properties, preset modes, and which sensors, switches, and buttons to create. Adding support for a new model should only require a new entry in `models.yaml`.

Entities are registered lazily: the integration waits for the device to report its model identifier over MQTT before creating entities, so `device_info` can contain the correct model name from the start.

## Limitations

- AC0651/10 support comes from the synced fork.
- AC0650/10 support is carried over from the original integration and should be tested on real hardware after the sync.
- The integration requires internet connectivity because it depends on the Philips/Versuni cloud service.

## License

MIT License. See LICENSE file for details.

## Disclaimer

Not affiliated with or endorsed by Philips or Versuni. Use at your own risk.
