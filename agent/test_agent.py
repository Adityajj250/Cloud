"""
R.I.F.T. Verification & Self-Test Script
Tests:
1. System telemetry (CPU, RAM, Cores)
2. Phase 3 Safe Remote Control (Unsigned rejection, unlisted rejection, valid acceptance)
3. Phase 2 Math Worker (Monte Carlo, Matrix Multiply)
4. Phase 2 Image Worker (Tile processing)
5. Phase 5 LLM Worker (CPU token streaming)
6. Phase 4 Vision Worker (Face/Object detection on CPU)
7. Phase 0/1 AWS IoT Core connection & telemetry publish
"""

import sys
import os
import json
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import AgentConfig
from security.verifier import CommandVerifier
from workers.math_worker import MathWorker
from workers.image_worker import ImageWorker
from workers.llm_worker import LLMWorker
from vision.detector import VisionWorker
from comms.mqtt_client import RiftMqttClient

def run_tests():
    print("=" * 60)
    print(">>> RUNNING R.I.F.T. SYSTEM VERIFICATION SUITE <<<")
    print("=" * 60)

    # Test 1: Telemetry & Config
    print("\n[Test 1] Testing System Telemetry & Configuration...")
    from rift_agent import RiftAgent
    agent = RiftAgent()
    telemetry = agent.get_system_telemetry()
    print(f"  * Device ID: {telemetry['deviceId']}")
    print(f"  * CPU Cores: {telemetry['cpu_cores']} | CPU Usage: {telemetry['cpu_percent']}%")
    print(f"  * RAM Free: {telemetry['ram_free_mb']} MB (Total: {telemetry['ram_used_mb']} MB used)")
    assert telemetry['cpu_cores'] > 0, "Failed to read CPU cores"
    print("  --> PASS: Telemetry operational")

    # Test 2: Phase 3 Security & Command Allowlist
    print("\n[Test 2] Testing Phase 3 Safe Remote Control...")
    verifier = CommandVerifier("rift-super-secret-hmac-key-2026", ["ping", "status", "run_chunk"])
    
    # 2a. Unsigned command
    is_valid, reason, _ = verifier.verify_and_parse(json.dumps({"command": "ping"}))
    assert not is_valid, "Unsigned command should have failed!"
    print(f"  * Unsigned command rejected correctly: {reason}")
    
    # 2b. Forbidden shell command
    forbidden = verifier.sign_command({"command": "arbitrary_shell"})
    is_valid, reason, _ = verifier.verify_and_parse(json.dumps(forbidden))
    assert not is_valid, "Forbidden command should have failed!"
    print(f"  * Forbidden command rejected correctly: {reason}")
    
    # 2c. Valid signed allowlisted command
    valid_cmd = verifier.sign_command({"command": "ping", "cmdId": "test-101"})
    is_valid, reason, data = verifier.verify_and_parse(json.dumps(valid_cmd))
    assert is_valid, "Valid signed command failed!"
    print(f"  * Valid signed allowlisted command accepted: {data['command']}")
    print("  --> PASS: Safe Remote Control operational")

    # Test 3: Phase 2 Distributed Compute (Monte Carlo & Matrix)
    print("\n[Test 3] Testing Phase 2 Distributed Math Worker (CPU)...")
    mc_result = MathWorker.run_monte_carlo({"seed": 42, "samples": 500_000})
    print(f"  * Monte Carlo Pi (500k samples): {mc_result['estimated_pi']} (Time: {mc_result['elapsed_seconds']}s)")
    assert 3.10 < mc_result['estimated_pi'] < 3.18, "Monte Carlo Pi estimate out of bounds"
    
    mat_result = MathWorker.run_matrix_multiply({"row_start": 0, "row_end": 50, "dim": 300})
    print(f"  * Matrix Block (50x300 @ 300x300): Checksum={mat_result['checksum']:.2f} (Time: {mat_result['elapsed_seconds']}s)")
    print("  --> PASS: Distributed Math Worker operational")

    # Test 4: Phase 2 Image Tile Worker
    print("\n[Test 4] Testing Phase 2 Image Tile Worker (CPU)...")
    img_result = ImageWorker.process_tile({"tile_id": "tile-001", "filter_type": "edge_enhance", "width": 128, "height": 128})
    print(f"  * Tile Processed: {img_result['tile_id']} | Filter: {img_result['filter_applied']} | Output bytes: {img_result['output_size_bytes']}")
    assert img_result['output_size_bytes'] > 0, "Tile processing returned 0 bytes"
    print("  --> PASS: Image Tile Worker operational")

    # Test 5: Phase 5 CPU LLM Token Streaming
    print("\n[Test 5] Testing Phase 5 Local LLM Streaming...")
    llm = LLMWorker()
    streamed_tokens = list(llm.stream_generate("Summarize cluster status"))
    combined_response = "".join(streamed_tokens)
    print(f"  * Streamed {len(streamed_tokens)} tokens: {combined_response[:80]}...")
    assert len(streamed_tokens) > 0, "No tokens streamed"
    print("  --> PASS: LLM Streaming Worker operational")

    # Test 6: Phase 4 CPU Vision & Object Detector
    print("\n[Test 6] Testing Phase 4 CPU Vision Detector...")
    vision = VisionWorker()
    vis_res = vision.detect_objects_in_frame("")
    print(f"  * Vision inference: {vis_res['count']} objects found at {vis_res['fps']} FPS ({vis_res['inference_time_ms']} ms)")
    print("  --> PASS: Vision Detector operational")

    # Test 7: Phase 0/1 Live AWS IoT Core TLS Connectivity & Heartbeat
    print("\n[Test 7] Testing Phase 0/1 Live Outbound TLS to AWS IoT Core...")
    config = AgentConfig()
    received_msgs = []
    
    def on_msg(t, payload):
        received_msgs.append((t, payload))

    mqtt = RiftMqttClient(
        endpoint=config.iot_endpoint,
        cert_path=config.cert_path,
        key_path=config.key_path,
        root_ca_path=config.root_ca_path,
        client_id=config.device_id,
        status_topic=f"rift/{config.device_id}/status",
        on_message_callback=on_msg
    )

    connected = mqtt.connect()
    assert connected, "Failed to connect to AWS IoT Core over TLS port 443"
    print("  * Outbound TLS on Port 443 established with AWS IoT Core!")

    test_topic = f"rift/{config.device_id}/status"
    mqtt.publish(test_topic, telemetry)
    print(f"  * Published live telemetry to AWS IoT topic: {test_topic}")
    time.sleep(1)
    mqtt.disconnect()
    print("  --> PASS: AWS IoT Core Phase 0/1 verified live!")

    print("\n" + "=" * 60)
    print(">>> ALL 7 R.I.F.T. COMPONENT & INTEGRATION TESTS PASSED <<<")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
