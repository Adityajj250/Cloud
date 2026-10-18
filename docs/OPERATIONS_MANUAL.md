# R.I.F.T. Operations & Execution Manual
**Remote Infrastructure & Federated Tasks**  
*Distributed computing, mobile control, and AI vision across separate LANs via AWS Control Plane*

---

## 1. Project Overview & Verified Architecture

R.I.F.T. allows PCs located on completely separate local networks (e.g., Home Wi-Fi, Office LAN, Mobile 4G/5G Hotspots) to form a unified compute cluster controlled from a mobile phone, without requiring port forwarding, static public IPs, or VPNs.

### Core Working Principle
Every worker PC establishes an **outbound-only TLS connection on port 443** to AWS IoT Core (`a30f3sdnp94spe-ats.iot.ap-southeast-2.amazonaws.com`). Outbound traffic automatically punches through NAT and firewalls on any standard consumer or corporate network.

---

## 2. Live Deployed AWS Infrastructure (ap-southeast-2)

All components are deployed and operating within **AWS Free Tier**:

| Component | AWS Resource Name | Status | Purpose |
| :--- | :--- | :--- | :--- |
| **IoT Core** | `RiftDevicePolicy`, Thing `rift-win-worker-01` | **Active** | MQTT over TLS (Port 443) control plane & heartbeat |
| **DynamoDB** | `rift-devices` | **Active** | Worker device registry, state, and telemetry |
| **DynamoDB** | `rift-jobs` | **Active** | Distributed job metadata, chunk progress, results |
| **DynamoDB** | `rift-chunks` | **Active** | Individual chunk tracking and retry counters |
| **DynamoDB** | `rift-audit` | **Active** | Security audit log of commands & execution outcomes |
| **S3 Storage** | `rift-storage-903859732456-ap-southeast-2` | **Active** | Private storage for image tiles, inputs, outputs |
| **SQS Queue** | `rift-chunks-queue` | **Active** | Distributed chunk work queue (VisibilityTimeout=120s) |
| **SQS DLQ** | `rift-chunks-dlq` | **Active** | Dead-letter queue for failed chunks after 3 retries |

---

## 3. How to Run the System

### Running the Worker Agent (PC Side)
Open PowerShell on your Windows machine and run:
```powershell
python c:\Users\Admin\Desktop\lan-share\agent\rift_agent.py
```
- Install the agent dependencies first when setting up a fresh Python environment: `python -m pip install -r c:\Users\Admin\Desktop\lan-share\agent\requirements.txt`.
- The agent prints a six-digit Screen Share pairing PIN at startup. Enter it in the phone's Screen Share tab to stream the desktop and send mouse/keyboard input. Stop the session from the phone when finished. The agent must run in the logged-in interactive Windows session; it cannot operate the lock screen.
- The agent immediately connects outbound to AWS IoT Core.
- It begins publishing real-time telemetry (CPU usage %, RAM available, battery state) to `rift/rift-win-worker-01/status`.
- It listens for signed commands on `rift/rift-win-worker-01/cmd`.

### Adding Additional PCs on Other LANs
1. Copy the `agent/` folder to the second or third PC.
2. In `agent/config.py`, change `device_id` to `rift-win-worker-02` (or `03`).
3. Run `python rift_agent.py`. The new node will immediately appear online in the cluster.

### Running the Mobile Command Center (PWA)
Install `cloudflared`, then run `powershell -ExecutionPolicy Bypass -File .\scripts\start_public_test.ps1` from the project folder. Open the temporary HTTPS URL printed by the script on the phone. On the laptop, open the same URL with `?view=laptop` appended for the laptop console. Keep the script running while testing; Ctrl+C stops access. Do not open the PWA file directly because it has no API connection that way.
- **Nodes Tab:** Shows real-time CPU load, available RAM, and online/offline status of all workers across LANs.
- **Compute Tab:** Submit Monte Carlo simulations, Matrix multiplications, or Image tile transformations. Watch chunk progress bars in real-time.
- **AI Vision Tab:** Captures phone camera (`getUserMedia`), transmits frames, and receives object/face detections processed by the worker's CPU.
- **Local LLM Tab:** Type prompts; execution runs on the server PC through Ollama. Responses return when generation finishes (the temporary Quick Tunnel does not support SSE token streaming).
- **Screen Share Tab:** Streams the laptop desktop at up to 1280×720 and about 5 frames per second with the cursor drawn into each frame. Full-screen requests landscape orientation and overlays a translucent virtual keyboard; the touchpad remains available from the screen controls. The laptop console shows whether a phone is controlling it.

---

## 4. Security & Safety Verification

All 7 security and computation tests have been executed and passed:
1. **Unsigned Command Rejection:** Verified that unsigned messages on MQTT are rejected with an explicit security alert.
2. **Forbidden Command Rejection:** Verified that arbitrary shell commands (e.g. `rm -rf`) are rejected and logged to audit.
3. **Valid Signed Command Execution:** Verified that HMAC-SHA256 signed allowlisted commands (`ping`, `status`, `run_chunk`, `start_llm`) execute successfully.
4. **Monte Carlo Worker:** Verified distributed estimation of $\pi$ across slices.
5. **Matrix Multiplication Worker:** Verified block row multiplication checksums.
6. **Image Tile Worker:** Verified tile filter processing on CPU.
7. **Local LLM:** Verify a completed response returns to the mobile client; token streaming is disabled for Quick Tunnel compatibility.
