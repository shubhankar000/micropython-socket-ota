
# MicroPython-Socket-OTA

MicroPython library for local network socket-based over-the-air (OTA) updates, inspired by Arduino ESP32's OTA functionality.

## Overview
- **`ota/__init__.py`**: Implements the OTA server.
- **`ota_client.py`**: Client script to trigger OTA from a development system.

## OTA Server: `OTAUpdater`
The `OTAUpdater` class in `ota/__init__.py` enables devices like ESP32 to act as OTA servers.

### Features
- Supports mDNS based hostname.
- SHA256-based authentication.
- Completely non-blocking, runs on a separate thread.

### Usage
```python
from ota import OTAUpdater
import _thread

config = {
    "WIFI_SSID": "YourSSID",
    "WIFI_PASSWORD": "YourPassword",
    "DEVICE_NAME": "YourDeviceName",
    "OTA_PASSWORD": "YourOTAPassword",
}

def start_ota():
    """Run OTA server in a separate thread"""
    updater = OTAUpdater()
    updater.connect_wifi()
    updater.start_server()

_thread.start_new_thread(start_ota, ())
```

### Limitations
- Supports only one client connection.
- Does not handle incomplete uploads.
- Limited to JSON metadata.
- Currently can only be triggered through the client script, not through an IDE or extension.

## OTA Client: `ota_client.py`
Transfers updates from a client machine to the OTA server.

### Features
- Resolves mDNS hostnames.
- Challenge-response authentication.
- Gathers files recursively with ignore patterns.
- Displays a progress bar.

### Command Example
```bash
python ota_client.py --host myesp32.local --port 8266 --src ./src --password my_secure_password
```

### Key Arguments
- `--host`: Device hostname or IP.
- `--port`: Server port (default: 8266).
- `--src`: Source directory (default: ./src).
- `--password`: OTA password (prompted if not provided).

### Limitations
- Requires Python 3.x.
- Needs `pymakr.conf` for ignore patterns.
- Does not resume interrupted uploads.
- Only tested on ESP32-S3, but should work on most micropython ports.

## Contributions
Contributions are welcome! Submit issues and pull requests on the GitHub repository.

## License
Licensed under the MIT License. See the `LICENSE` file for details.
