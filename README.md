# MicroPython-Socket-OTA with Compression

A MicroPython library for socket-based over-the-air (OTA) updates using compressed data streams. This implementation improves update speed and reduces overhead by bundling and compressing files before transfer.

## Overview
- **`ota/__init__.py`**: Implements the OTA server for ESP32 and similar devices.
- **`ota_client.py`**: Client script to compress and send updates from a development system.

## Key Features

### OTA Server: `OTAUpdater`
- **WiFi Setup**: Configure and connect to a WiFi network.
- **Challenge-Response Authentication**: Ensures secure updates using SHA256.
- **Compressed Updates**: Receives, decompresses, and applies updates in a single operation.
- **Device Reboot**: Automatically reboots after a successful update.

#### Usage Example
```python
from ota import OTAUpdater

config = {
    "WIFI_SSID": "YourSSID",
    "WIFI_PASSWORD": "YourPassword",
    "OTA_PASSWORD": "YourOTAPassword",
}

ota_updater = OTAUpdater()
ota_updater.start_server()
```

#### Limitations
- Supports only one client connection at a time.
- Assumes sufficient disk space for compressed and decompressed data.

---

### OTA Client: `ota_client.py`
- **File Compression**: Combines and compresses files into a single stream for efficiency.
- **Progress Display**: Shows upload progress using a progress bar.
- **Challenge-Response Authentication**: Authenticates with the server using a secure SHA256-based method.
- **Automatic File Filtering**: Ignores files based on patterns specified in `pymakr.conf`.

#### Command Example
```bash
python ota_client.py --host myesp32.local --port 8266 --src ./src --password my_secure_password
```

#### Key Arguments
- `--host`: Device hostname or IP (e.g., `myesp32.local`).
- `--port`: OTA server port (default: 8266).
- `--src`: Path to the source directory (default: `./src`).
- `--password`: OTA password (will prompt if not provided).

---

## How It Works

### Compression and Transmission
1. **Client**:
   - Collects all files from the specified source directory.
   - Compresses the files into a single Deflate stream.
   - Sends metadata and compressed data to the server.
2. **Server**:
   - Authenticates the client.
   - Receives metadata and validates available space.
   - Decompresses the update and applies the changes.
   - Reboots the device after a successful update.

### Performance Improvements
- **Compression**: Significantly reduces data size, improving transfer speed.
- **Single Stream**: Reduces overhead by sending files in a single operation.

---

## Contributions
Contributions are welcome! Submit issues and pull requests on the [GitHub repository](#).

## License
Licensed under the MIT License. See the `LICENSE` file for details.
