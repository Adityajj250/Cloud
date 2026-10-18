"""
R.I.F.T. Agent Configuration (Windows CPU)
"""

import os
from dataclasses import dataclass, field
from typing import List

from identity import load_device_identity

_DEVICE_ID, _DEVICE_NAME = load_device_identity()

@dataclass
class AgentConfig:
    # Device Identity
    device_id: str = field(default_factory=lambda: os.getenv("RIFT_DEVICE_ID") or _DEVICE_ID)
    device_name: str = field(default_factory=lambda: os.getenv("RIFT_DEVICE_NAME") or _DEVICE_NAME)
    server_url: str = field(default_factory=lambda: (os.getenv("RIFT_SERVER_URL") or "http://127.0.0.1:8765").rstrip("/"))
    
    # AWS IoT Core Settings
    iot_endpoint: str = os.getenv("RIFT_IOT_ENDPOINT", "a30f3sdnp94spe-ats.iot.ap-southeast-2.amazonaws.com")
    iot_port: int = 443  # ALPN MQTT over TLS
    cert_path: str = field(default_factory=lambda: os.getenv("RIFT_IOT_CERT") or os.path.join(os.path.dirname(__file__), "certs", "device.pem.crt"))
    key_path: str = field(default_factory=lambda: os.getenv("RIFT_IOT_PRIVATE_KEY") or os.path.join(os.path.dirname(__file__), "certs", "device.private.key"))
    root_ca_path: str = field(default_factory=lambda: os.getenv("RIFT_IOT_ROOT_CA") or os.path.join(os.path.dirname(__file__), "certs", "AmazonRootCA1.pem"))
    
    # Heartbeat Settings (tuned to stay comfortably inside Free Tier)
    heartbeat_interval_sec: int = int(os.getenv("RIFT_HEARTBEAT_SEC", "30"))
    
    # Allowed Commands (Strict allowlist - Phase 3)
    allowed_commands: List[str] = field(default_factory=lambda: [
        "ping",
        "status",
        "run_chunk",
        "start_llm",
        "stop_llm",
        "restart_agent",
    ])
    
    # Shared secret key for HMAC command signature verification
    command_hmac_secret: str = os.getenv("RIFT_HMAC_SECRET", "rift-super-secret-hmac-key-2026")
    
    # S3 and SQS work parameters
    aws_region: str = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-2")
    sqs_queue_url: str = os.getenv("RIFT_SQS_QUEUE_URL", "https://sqs.ap-southeast-2.amazonaws.com/903859732456/rift-chunks-queue")
    s3_bucket: str = os.getenv("RIFT_S3_BUCKET", "rift-storage-903859732456-ap-southeast-2")
    
    # Local LLM (Ollama or llama.cpp CPU)
    ollama_url: str = os.getenv("RIFT_OLLAMA_URL", "http://localhost:11434")
    default_llm_model: str = os.getenv("RIFT_LLM_MODEL", "qwen2.5:3b")
