# Philips Air+ Home Assistant Integration

This custom integration allows you to control your Philips Air+ AC0650/10 air purifier through Home Assistant. It communicates with the Philips/Versuni cloud service using the same protocol as the official mobile app.

## Features

- **Fan Control**: Control fan speed (Auto, Sleep, Turbo)
- **Power Control**: Turn the air purifier on and off
- **Filter Monitoring**: Monitor filter life for both replace and clean filters
- **Real-time Updates**: Receive real-time status updates via MQTT

## Supported Devices

- Philips Air+ AC0650/10 (tested)
- Other Air+ models very unlikely to work!! (Most likely could be easily ported)

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

You need a Philips Air+ account with your device already set up in the official mobile app.

### Authentication Methods

#### Method 1: OAuth PKCE Flow

1. Login to the Philips website, on the website present in HA when configuring
2. After login the last request (check on dev tools) will be similar to this: com.philips.air://loginredirect?code=st2.xxxxxxx.sc3&state=xxxx
3. Copy the code from the request
4. Go to the integration configuration in HA and paste the code

## Development

This integration is based on reverse-engineering the Philips Air+ mobile app protocol.

## Limitations

- Only tested with AC0650/10 model
- Requires internet connectivity (cloud-dependent)

## License

This integration is released under the MIT License. See LICENSE file for details.

## Disclaimer

This integration is not affiliated with or endorsed by Philips or Versuni. It is a third-party implementation based on reverse-engineering their API. Use at your own risk.

## Support

- **Issues**: Report bugs and feature requests on GitHub

### Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request
