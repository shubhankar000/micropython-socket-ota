import _thread
import hashlib
import json
import os
import random
import socket
from time import sleep

import machine
import network
import ubinascii

try:
    from config.settings import env
    has_env = True
except ImportError:
    has_env = False


class OTAUpdater:
    def __init__(self, config=None):
        """Initialize OTA updater with either env vars or config dict
        
        Args:
            config (dict, optional): Configuration dictionary containing:
                - WIFI_SSID: WiFi network name
                - WIFI_PASSWORD: WiFi password 
                - DEVICE_NAME: Device hostname
                - OTA_PASSWORD: Password for OTA updates
        """
        if not has_env and not config:
            raise ValueError("Either env module or config dict must be provided")
            
        self.config = config if config else {
            "WIFI_SSID": env.WIFI_SSID,
            "WIFI_PASSWORD": env.WIFI_PASSWORD,
            "DEVICE_NAME": env.DEVICE_NAME,
            "OTA_PASSWORD": env.OTA_PASSWORD
        }
        
        self.wifi = network.WLAN(network.STA_IF)
        self.thread_id = _thread.get_ident()

    def _log(self, message):
        """Helper method for consistent logging with thread ID"""
        print(f"[Thread-{self.thread_id}] {message}")

    def setup_hostname(self):
        """Setup device hostname for mDNS"""
        try:
            self._log("Setting up hostname...")
            network.hostname(self.config["DEVICE_NAME"])
            self._log(f"Hostname set to: {network.hostname()}")
            self._log(f"Device should be accessible at {network.hostname()}.local")
        except Exception as e:
            self._log(f"Hostname setup failed: {e}")
            raise

    def _generate_challenge(self):
        """Generate a random challenge string"""
        self._log("Generating authentication challenge...")
        challenge = ubinascii.hexlify(
            random.getrandbits(32).to_bytes(4, "big")
        ).decode()
        self._log(f"Challenge generated: {challenge}")
        return challenge

    def _verify_response(self, challenge, response):
        """Verify the client's response to our challenge"""
        self._log("Verifying client response...")
        h = hashlib.sha256((challenge + self.config["OTA_PASSWORD"]).encode())
        expected = ubinascii.hexlify(h.digest()).decode()
        result = response == expected
        self._log(f"Authentication {'successful' if result else 'failed'}")
        return result

    def connect_wifi(self):
        self._log("Initializing WiFi connection...")
        self.setup_hostname()

        if not self.wifi.isconnected():
            self._log("WiFi not connected. Attempting connection...")
            self.wifi.active(True)
            self.wifi.connect(self.config["WIFI_SSID"], self.config["WIFI_PASSWORD"])
            while not self.wifi.isconnected():
                self._log("Waiting for WiFi connection...")
                sleep(1)
        self._log(f"Network config: {self.wifi.ifconfig()}")
        self._log(f"Device is now accessible at {network.hostname()}.local")

    def start_server(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("", 8266))
        sock.listen(1)
        self._log("OTA server started on port 8266")

        while True:
            try:
                conn, addr = sock.accept()
                conn.settimeout(10)  # Add 10 second timeout
                self._log(f"Client connected from {addr}")

                try:
                    self._handle_client_connection(conn)
                except Exception as e:
                    self._log(f"Error handling client: {e}")
                finally:
                    conn.close()
                    self._log("Connection closed")
            except Exception as e:
                self._log(f"Server error: {e}")

    def _handle_client_connection(self, conn):
        """Handle a single client connection"""
        try:
            # Authentication
            if not self._authenticate_client(conn):
                return

            # Receive metadata
            try:
                # Get metadata size
                self._log("Waiting for metadata size...")
                metadata_size = int(conn.recv(10).decode().strip())
                conn.send(b"OK")
                self._log(f"Expecting {metadata_size} bytes of metadata")

                # Receive metadata in chunks
                metadata = b""
                remaining = metadata_size
                while remaining > 0:
                    chunk = conn.recv(min(1024, remaining))
                    if not chunk:
                        raise Exception("Connection closed while receiving metadata")
                    metadata += chunk
                    remaining -= len(chunk)
                    self._log(f"Received metadata chunk: {len(chunk)} bytes")

                metadata = json.loads(metadata.decode())
                self._log(f"Received metadata: {metadata}")
                conn.send(b"OK")

                # Process each file
                for file_info in metadata["files"]:
                    self._log(f"Processing file: {file_info['path']}")
                    success = self._receive_file(conn, file_info)
                    if not success:
                        self._log(f"Failed to receive file: {file_info['path']}")
                        return

                # Send final success message before reboot
                self._log("Update successful, sending final confirmation...")
                conn.send(b"UPDATE_SUCCESS")

                conn.close()

                # Small delay to allow client to receive the message
                sleep(0.5)

                self._log("Rebooting device...")
                machine.reset()

            except Exception as e:
                self._log(f"Error processing update: {e}")
                conn.send(b"FAIL")

        except Exception as e:
            self._log(f"Error processing update: {e}")
            try:
                conn.send(b"FAIL")
            except Exception:
                pass

    def _makedirs(self, path):
        """Create directories recursively (like os.makedirs)"""
        if path == "":
            return

        try:
            os.mkdir(path)
        except OSError as e:
            if e.args[0] == 17:  # EEXIST
                pass
            else:
                raise

    def _receive_file(self, conn, file_info):
        file_path = file_info["path"]
        file_size = file_info["size"]
        self._log(f"Receiving file {file_path} ({file_size} bytes)")

        # Create directories if they don't exist
        try:
            dir_path = file_path.rsplit("/", 1)[0] if "/" in file_path else ""
            if dir_path:  # Only create dirs if there's actually a path
                parts = dir_path.split("/")
                path = ""
                for part in parts:
                    path = path + part + "/"
                    self._makedirs(path[:-1])  # Remove trailing slash
            self._log(f"Created directory structure for {file_path}")
        except OSError as e:
            self._log(f"Error creating directory structure: {e}")
            return False

        # Receive and write file
        received = 0
        with open(file_path, "wb") as f:
            while received < file_size:
                chunk = conn.recv(min(1024, file_size - received))
                if not chunk:
                    self._log(f"Connection lost while receiving {file_path}")
                    return False
                f.write(chunk)
                received += len(chunk)
                if received % 4096 == 0:  # Log progress every 4KB
                    self._log(f"Received {received}/{file_size} bytes")

        self._log(f"File {file_path} received successfully")
        conn.send(b"OK")
        return True

    def _authenticate_client(self, conn):
        """Authenticate client using challenge-response with max 3 attempts"""
        auth_attempts = 0
        max_attempts = 3

        while auth_attempts < max_attempts:
            auth_attempts += 1
            self._log(f"Authentication attempt {auth_attempts}/{max_attempts}")

            try:
                # Generate and send challenge
                challenge = self._generate_challenge()
                self._log("Sending challenge to client...")
                conn.send(challenge.encode())

                # Get response
                self._log("Waiting for client response...")
                response = conn.recv(64).decode()  # SHA256 is 64 chars

                if self._verify_response(challenge, response):
                    conn.send(b"OK")
                    self._log("Authentication successful")
                    return True
                else:
                    conn.send(b"AUTH_FAIL")
                    self._log(
                        f"Authentication failed (attempt {auth_attempts}/{max_attempts})"
                    )

            except Exception as e:
                self._log(f"Authentication error: {e}")
                return False

        self._log("Maximum authentication attempts reached")
        return False
