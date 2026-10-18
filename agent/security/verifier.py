"""
R.I.F.T. Command Verifier (Phase 3 Safe Remote Control)
Verifies HMAC-SHA256 signatures and checks commands against the strict allowlist.
"""

import hmac
import hashlib
import json
import logging
from typing import Tuple, Dict, Any

logger = logging.getLogger("rift.security")

class CommandVerifier:
    def __init__(self, secret_key: str, allowed_commands: list):
        self.secret_key = secret_key.encode("utf-8")
        self.allowed_commands = set(allowed_commands)

    def verify_and_parse(self, raw_payload: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validates:
        1. JSON payload format
        2. Command is in allowed list
        3. Signature matches HMAC-SHA256(canonical_message, secret)
        """
        try:
            data = json.loads(raw_payload)
        except Exception as e:
            return False, f"Invalid JSON payload: {e}", {}

        cmd = data.get("command")
        if not cmd:
            return False, "Missing 'command' field in payload", data

        # 1. Allowlist Check
        if cmd not in self.allowed_commands:
            return False, f"Command '{cmd}' is NOT in allowed list {list(self.allowed_commands)}", data

        # 2. Signature Check
        signature = data.get("signature")
        if not signature:
            return False, "Command rejected: Missing 'signature'", data

        # Construct payload string to sign (excluding 'signature')
        payload_copy = {k: v for k, v in data.items() if k != "signature"}
        canonical_str = json.dumps(payload_copy, sort_keys=True)
        
        expected_sig = hmac.new(self.secret_key, canonical_str.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(signature, expected_sig):
            return False, "Command rejected: Invalid HMAC signature", data

        return True, "Valid", data

    def sign_command(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Utility for backend/operator to sign a command payload."""
        payload_copy = {k: v for k, v in payload.items() if k != "signature"}
        canonical_str = json.dumps(payload_copy, sort_keys=True)
        sig = hmac.new(self.secret_key, canonical_str.encode("utf-8"), hashlib.sha256).hexdigest()
        payload_copy["signature"] = sig
        return payload_copy
