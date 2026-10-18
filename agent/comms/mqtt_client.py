"""
R.I.F.T. MQTT Client wrapper for AWS IoT Core
Uses AWS IoT Device SDK v2 (awscrt) with ALPN MQTT on TLS port 443.
Implements Last-Will-and-Testament on rift/{deviceId}/status.
"""

import json
import logging
import time
from typing import Callable, Optional
from awscrt import io, mqtt, auth, http
from awsiot import mqtt_connection_builder

logger = logging.getLogger("rift.mqtt")

class RiftMqttClient:
    def __init__(
        self,
        endpoint: str,
        cert_path: str,
        key_path: str,
        root_ca_path: str,
        client_id: str,
        status_topic: str,
        on_message_callback: Callable[[str, str], None]
    ):
        self.endpoint = endpoint
        self.cert_path = cert_path
        self.key_path = key_path
        self.root_ca_path = root_ca_path
        self.client_id = client_id
        self.status_topic = status_topic
        self.on_message_callback = on_message_callback
        self.connection: Optional[mqtt.Connection] = None
        self.connected = False

    def connect(self) -> bool:
        try:
            logger.info(f"Connecting to AWS IoT endpoint {self.endpoint} as {self.client_id}...")
            
            # Configure Last Will and Testament (LWT)
            # When the connection drops ungracefully, AWS IoT publishes this offline payload
            lwt_payload = json.dumps({
                "deviceId": self.client_id,
                "status": "offline",
                "timestamp": int(time.time()),
                "reason": "connection_lost"
            })
            
            will = mqtt.Will(
                topic=self.status_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                payload=lwt_payload.encode("utf-8"),
                retain=False
            )

            # Build TLS connection via port 443 with ALPN
            self.connection = mqtt_connection_builder.mtls_from_path(
                endpoint=self.endpoint,
                port=443,
                cert_filepath=self.cert_path,
                pri_key_filepath=self.key_path,
                ca_filepath=self.root_ca_path,
                client_id=self.client_id,
                clean_session=False,
                keep_alive_secs=60,
                will=will,
                on_connection_interrupted=self._on_connection_interrupted,
                on_connection_resumed=self._on_connection_resumed
            )

            connect_future = self.connection.connect()
            connect_future.result(timeout=15)
            self.connected = True
            logger.info("Connected to AWS IoT Core successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to AWS IoT Core: {e}")
            self.connected = False
            return False

    def subscribe(self, topic: str):
        if not self.connection or not self.connected:
            return
        
        import threading
        def _on_msg(topic, payload, dup, qos, retain, **kwargs):
            try:
                msg_str = payload.decode("utf-8")
                # Offload message handling to a daemon thread so the AWS CRT I/O loop is never blocked
                threading.Thread(
                    target=self.on_message_callback,
                    args=(topic, msg_str),
                    daemon=True
                ).start()
            except Exception as e:
                logger.error(f"Error dispatching message on {topic}: {e}")

        logger.info(f"Subscribing to topic: {topic}")
        sub_future, _ = self.connection.subscribe(
            topic=topic,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=_on_msg
        )
        sub_future.result(timeout=10)

    def publish(self, topic: str, payload_dict: dict):
        if not self.connection or not self.connected:
            return
        payload_bytes = json.dumps(payload_dict).encode("utf-8")
        pub_future, _ = self.connection.publish(
            topic=topic,
            payload=payload_bytes,
            qos=mqtt.QoS.AT_LEAST_ONCE
        )
        pub_future.result(timeout=10)

    def _on_connection_interrupted(self, connection, error, **kwargs):
        logger.warning(f"Connection interrupted: {error}")
        self.connected = False

    def _on_connection_resumed(self, connection, return_code, session_present, **kwargs):
        logger.info(f"Connection resumed with return_code: {return_code}, session_present: {session_present}")
        self.connected = True

    def disconnect(self):
        if self.connection and self.connected:
            # Publish graceful offline status before shutting down
            graceful_offline = {
                "deviceId": self.client_id,
                "status": "offline",
                "timestamp": int(time.time()),
                "reason": "agent_shutdown"
            }
            try:
                self.publish(self.status_topic, graceful_offline)
            except Exception:
                pass
            disc_future = self.connection.disconnect()
            disc_future.result(timeout=5)
            self.connected = False
            logger.info("Disconnected from AWS IoT Core.")
