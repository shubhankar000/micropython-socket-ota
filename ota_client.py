import argparse
import getpass
import hashlib
import io
import json
import socket
import sys
from pathlib import Path

from tqdm.auto import tqdm


class OTAClient:
    def __init__(self, host, port, src_path, password=None):
        self.host = self._resolve_mdns(host) if host.endswith(".local") else host
        self.port = port
        self.src_path = Path(src_path).resolve()
        self.py_ignore = self._load_pymakr_config()
        self.password = password

    def _resolve_mdns(self, hostname):
        """Resolve .local hostname to IP address"""
        self._log(f"Attempting to resolve hostname: {hostname}")
        try:
            ip = socket.gethostbyname(hostname)
            self._log(f"Resolved to IP: {ip}")
            return ip
        except socket.gaierror as e:
            self._log(f"DNS resolution failed: {e}")
            raise Exception(f"Could not resolve {hostname}")

    def _load_pymakr_config(self):
        """Load py_ignore patterns from pymakr.conf"""
        pymakr_path = self.src_path / "pymakr.conf"
        self._log(f"Loading pymakr config from: {pymakr_path}")
        try:
            with pymakr_path.open("r") as f:
                config = json.load(f)
                ignore_patterns = config.get("py_ignore", [])
                self._log(f"Loaded ignore patterns: {ignore_patterns}")
                return ignore_patterns
        except (FileNotFoundError, json.JSONDecodeError) as e:
            self._log(f"Error loading pymakr.conf: {e}")
            sys.exit(1)

    def _log(self, message):
        """Helper method for consistent logging"""
        print(f"[OTAClient] {message}")

    def _authenticate(self, sock):
        """Perform challenge-response authentication"""
        attempt = 0
        max_attempts = 3

        while attempt < max_attempts:
            attempt += 1
            self._log(f"Authentication attempt {attempt}/{max_attempts}")

            # Use stored password if available, otherwise prompt
            password = self.password or getpass.getpass("Enter OTA password: ")

            try:
                # Receive challenge
                challenge = sock.recv(8).decode()

                # Calculate response
                response = hashlib.sha256((challenge + password).encode()).hexdigest()

                # Send response
                sock.send(response.encode())

                # Check if authentication succeeded
                result = sock.recv(9)

                if result == b"OK":
                    self._log("Authentication successful")
                    return True
                elif result == b"AUTH_FAIL":
                    self._log("Authentication failed")
                else:
                    self._log("Unexpected server response")
                    return False

                # Test if connection is still alive
                try:
                    sock.getpeername()
                except OSError:
                    self._log("Server closed connection")
                    return False

            except (ConnectionError, OSError) as e:
                self._log(f"Connection error: {e}")
                return False

        self._log("Maximum authentication attempts reached")
        return False

    def should_ignore(self, path: Path) -> bool:
        """Check if path should be ignored based on py_ignore rules"""
        rel_path = path.relative_to(self.src_path).as_posix()
        should_ignore = any(pattern in rel_path for pattern in self.py_ignore)
        if should_ignore:
            self._log(f"Ignoring file: {rel_path}")
        return should_ignore

    def gather_files(self):
        """Recursively gather all files to be uploaded"""
        self._log(f"Gathering files from: {self.src_path}")
        files_info = []

        for file_path in self.src_path.rglob("*"):
            if not file_path.is_file():
                continue

            try:
                if not self.should_ignore(file_path):
                    rel_path = file_path.relative_to(self.src_path).as_posix()
                    size = file_path.stat().st_size
                    self._log(f"Adding file: {rel_path} ({size} bytes)")
                    files_info.append(
                        {
                            "path": rel_path,
                            "size": size,
                            "full_path": str(file_path),
                        }
                    )
            except ValueError:
                self._log(f"Skipping file outside source path: {file_path}")
                continue

        self._log(f"Found {len(files_info)} files to upload")
        return files_info

    def send_update(self):
        files_info = self.gather_files()
        if not files_info:
            self._log("No files to upload")
            return

        # Create a BytesIO buffer for the compressed data
        compressed_buffer = io.BytesIO()

        # Create a deflate compressor with maximum window size (15)
        import zlib

        compressor = zlib.compressobj(
            level=9, wbits=-15
        )  # Negative wbits for raw deflate format

        total_uncompressed = 0

        # Compress all files into a single stream
        self._log("Compressing files...")
        for file_info in files_info:
            with open(file_info["full_path"], "rb") as f:
                data = f.read()
                total_uncompressed += len(data)
                compressed_buffer.write(compressor.compress(data))

        # Finish compression
        compressed_buffer.write(compressor.flush())
        compressed_size = compressed_buffer.tell()
        compressed_buffer.seek(0)

        self._log(
            f"Compressed size: {compressed_size} bytes (from {total_uncompressed} bytes) "
            f"({100 - compressed_size / total_uncompressed * 100:.2f}% reduction)"
        )

        # Prepare metadata
        metadata = {
            "compressed_size": compressed_size,
            "total_uncompressed": total_uncompressed,
            "files": files_info,
        }
        metadata_bytes = json.dumps(metadata).encode()

        # Connect and authenticate
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        try:
            self._log(f"Connecting to {self.host}:{self.port}...")
            sock.connect((self.host, self.port))

            if not self._authenticate(sock):
                self._log("Authentication failed, aborting update")
                return

            # Send metadata
            sock.send(f"{len(metadata_bytes):10d}".encode())
            response = sock.recv(2)
            if response != b"OK":
                self._log(f"Failed to send metadata size: {response}")
                return

            sock.send(metadata_bytes)
            response = sock.recv(2)
            if response != b"OK":
                self._log(f"Failed to send metadata: {response}")
                return

            # Send compressed data with progress bar
            with tqdm(
                total=compressed_size, desc="Uploading", unit="Bytes", unit_scale=True
            ) as pbar:
                while True:
                    chunk = compressed_buffer.read(1024)
                    if not chunk:
                        break
                    sock.send(chunk)
                    pbar.update(len(chunk))

            # Wait for final confirmation
            self._log("Waiting for final confirmation...")
            final_response = sock.recv(14)
            if final_response == b"UPDATE_SUCCESS":
                self._log("Update completed successfully! Device is rebooting.")
            else:
                self._log(f"Unexpected final response: {final_response}")

        except Exception as e:
            self._log(f"Error during update: {e}")
        finally:
            sock.close()
            compressed_buffer.close()


def main():
    parser = argparse.ArgumentParser(description="OTA Update Client")
    parser.add_argument(
        "--host",
        help="ESP32 IP address or hostname (e.g., myesp32.local)",
        required=True,
    )
    parser.add_argument(
        "--port", type=int, default=8266, help="OTA port (default: 8266)"
    )
    parser.add_argument(
        "--src",
        type=str,
        default="./src",
        help="Source directory path (default: ./src)",
    )
    parser.add_argument(
        "--password", type=str, help="OTA password (if not provided, will prompt)"
    )

    args = parser.parse_args()

    client = OTAClient(args.host, args.port, args.src, args.password)
    client.send_update()


if __name__ == "__main__":
    main()
