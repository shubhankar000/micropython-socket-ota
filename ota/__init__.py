import hashlib
import json
import os
import random
import socket
from tempfile import NamedTemporaryFile  # type: ignore
from time import sleep

import deflate
import machine
import network
import ubinascii

from config import env
from logger import get_logger


class OTAUpdater:
    def __init__(self):
        self.wifi = network.WLAN(network.STA_IF)
        self.logger = get_logger()

    def _generate_challenge(self):
        """Generate a random challenge string"""
        self.logger.info("Generating authentication challenge...")
        challenge = ubinascii.hexlify(
            random.getrandbits(32).to_bytes(4, "big")
        ).decode()
        self.logger.info(f"Challenge generated: {challenge}")
        return challenge

    def _verify_response(self, challenge, response):
        """Verify the client's response to our challenge"""
        self.logger.info("Verifying client response...")
        h = hashlib.sha256((challenge + env.OTA_PASSWORD).encode())
        expected = ubinascii.hexlify(h.digest()).decode()
        result = response == expected
        self.logger.info(f"Authentication {'successful' if result else 'failed'}")
        return result

    def start_server(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("", 8266))
        sock.listen(1)
        self.logger.info("OTA server started on port 8266")

        while True:
            try:
                conn, addr = sock.accept()
                conn.settimeout(10)  # Add 10 second timeout
                self.logger.info(f"Client connected from {addr}")

                try:
                    self._handle_client_connection(conn)
                except Exception as e:
                    self.logger.error(f"Error handling client: {e}")
                finally:
                    conn.close()
                    self.logger.info("Connection closed")

            except Exception as e:
                self.logger.error(f"Server error: {e}")

    def _handle_client_connection(self, conn):
        """Handle a single client connection"""
        try:
            # Authentication
            if not self._authenticate_client(conn):
                return

            # Receive metadata
            try:
                # Get metadata size
                self.logger.info("Waiting for metadata size...")
                metadata_size = int(conn.recv(10).decode().strip())
                conn.send(b"OK")
                self.logger.info(f"Expecting {metadata_size} bytes of metadata")

                # Receive metadata
                metadata = self._receive_data(conn, metadata_size)
                metadata = json.loads(metadata.decode())
                self.logger.info("Received metadata")
                conn.send(b"OK")

                # Check available space
                required_space = (
                    metadata["compressed_size"] + metadata["total_uncompressed"]
                )
                available_space = self._get_free_space()
                if available_space < required_space:
                    self.logger.error(
                        f"Insufficient space. Need {required_space}, have {available_space}"
                    )
                    conn.send(b"FAIL")
                    return

                # Create temporary file for compressed data
                with NamedTemporaryFile(suffix=".deflate", delete=True) as temp_file:
                    self.logger.info(f"Created temporary file: {temp_file.name}")

                    # Receive compressed data
                    received = 0
                    compressed_size = metadata["compressed_size"]
                    while received < compressed_size:
                        chunk = conn.recv(min(1024, compressed_size - received))
                        if not chunk:
                            raise Exception("Connection lost while receiving data")
                        temp_file.write(chunk)
                        received += len(chunk)
                        if received % 4096 == 0:
                            self.logger.info(
                                f"Received {received}/{compressed_size} bytes"
                            )

                    temp_file.flush()

                    # Close and reopen the file in binary read mode
                    temp_name = temp_file.name

                    # Now open the file for reading
                    with open(temp_name, "rb") as read_file:
                        self.logger.info("Compressed data received successfully")
                        self.logger.info("Starting decompression...")

                        with deflate.DeflateIO(read_file, deflate.RAW, 15) as d:
                            for file_info in metadata["files"]:
                                path = file_info["path"]
                                size = file_info["size"]
                                self.logger.info(f"Extracting: {path} ({size} bytes)")

                                # Create directories if needed
                                dir_path = path.rsplit("/", 1)[0] if "/" in path else ""
                                if dir_path:
                                    self._makedirs(dir_path)

                                # Read and write file
                                with open(path, "wb") as f:
                                    remaining = size
                                    while remaining > 0:
                                        chunk = d.read(min(1024, remaining))
                                        if not chunk:
                                            raise Exception(
                                                f"Unexpected EOF while extracting {path}"
                                            )
                                        f.write(chunk)
                                        remaining -= len(chunk)

                    self.logger.info("All files extracted successfully")

                # Send final success message before reboot
                conn.send(b"UPDATE_SUCCESS")
                conn.close()

                # Small delay to allow client to receive the message
                sleep(0.5)

                self.logger.info("Rebooting device...")
                machine.reset()

            except Exception as e:
                self.logger.error(f"Error processing update: {e}")
                conn.send(b"FAIL")

        except Exception as e:
            self.logger.error(f"Error processing update: {e}")
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

    def _get_free_space(self):
        """Get available space in bytes"""
        try:
            stats = os.statvfs("/")
            return stats[0] * stats[3]  # block size * free blocks
        except Exception:
            return float("inf")  # If can't determine, assume enough space

    def _receive_data(self, conn, size):
        """Helper to receive exact amount of data"""
        data = b""
        remaining = size
        while remaining > 0:
            chunk = conn.recv(min(1024, remaining))
            if not chunk:
                raise Exception("Connection closed while receiving data")
            data += chunk
            remaining -= len(chunk)
        return data

    def _authenticate_client(self, conn):
        """Authenticate client using challenge-response with max 3 attempts"""
        auth_attempts = 0
        max_attempts = 3

        while auth_attempts < max_attempts:
            auth_attempts += 1
            self.logger.info(
                "Authentication attempt %d/%d", auth_attempts, max_attempts
            )

            try:
                challenge = self._generate_challenge()
                self.logger.debug("Sending challenge to client...")
                conn.send(challenge.encode())

                self.logger.debug("Waiting for client response...")
                response = conn.recv(64).decode()

                if self._verify_response(challenge, response):
                    conn.send(b"OK")
                    self.logger.info("Authentication successful")
                    return True
                else:
                    conn.send(b"AUTH_FAIL")
                    self.logger.warning(
                        "Authentication failed (attempt %d/%d)",
                        auth_attempts,
                        max_attempts,
                    )

            except Exception as e:
                self.logger.error("Authentication error: %s", e)
                return False

        self.logger.error("Maximum authentication attempts reached")
        return False
