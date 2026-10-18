# R.I.F.T.

R.I.F.T. is a development-stage remote command center for one or more computers. Phone and laptop browsers connect to a hub; Windows agents provide telemetry, compute work, and consent-based remote screen control. The server handles the web API and phone-camera processing. AWS services coordinate worker commands and distributed jobs. A temporary Cloudflare Tunnel lets clients and agents reach the local hub from other networks without router port forwarding.

> **Development project:** defaults in the source are for one test setup. The server contains fixed AWS resource names, a fixed AWS CLI profile, and a development HMAC secret. The public test tunnel exposes the server to anyone with its URL. Do not use these defaults with sensitive data or as a production deployment.

## Features

| Area | What it does |
| --- | --- |
| **Fleet** | Lists all computers connected to the hub, with stable IDs, capabilities, telemetry, online state, and current controller. Known devices are retained in a local SQLite registry. |
| **Laptop console** | Opens with `?view=laptop`. Shows connected mobile clients, lets the laptop ping them, displays the phone camera preview, shows remote-control status, and lists compute jobs. |
| **Compute** | Submits chunked Monte Carlo, matrix multiplication, or image-tile jobs. The server records jobs/chunks in DynamoDB and enqueues chunks in SQS; the agent polls for work, executes it on CPU, and stores results for aggregation. |
| **Vision** | Captures frames from the phone's rear camera in the browser, sends them to the server over HTTPS, and runs OpenCV Haar-cascade face detection. Detection results and a camera preview are sent to the laptop console. This is face detection, not general-purpose object recognition. |
| **LLM** | Sends prompts to Ollama running on the server/laptop, using `qwen2.5:3b` by default. The response is returned when generation completes; the public Quick Tunnel path does not stream tokens to the browser. |
| **Screen Share** | Selects a computer and pairs with its Windows agent using the six-digit PIN printed by that agent. Streams at up to 1280×720 (about five frames per second) and relays mouse and virtual-keyboard input. Each computer has one controller; different phones can control different computers. |
| **Files (development)** | After pairing to a computer in Screen Share, browse/search its shared files, preview or download supported files, and upload files to its inbox. Uploads are chunked and can resume in the same browser session. The agent creates `RIFT Shared`, `RIFT Inbox`, and `RIFT Inbox\Quarantine` under its Windows user profile. Risky executable/script extensions are quarantined; R.I.F.T. never runs uploaded files. |
| **Cross-network access** | The Quick Tunnel publishes the local web server through a temporary HTTPS URL. The worker agent also connects outbound to AWS IoT Core over TLS, so the phone and laptop do not have to share a Wi-Fi network. |
| **Android wrapper** | A native Android WebView shell opens the same HTTPS PWA and supports camera permission requests, file selection/upload, downloads, and fullscreen video. The browser PWA is the simplest way to test features. |

## Architecture

```text
Phone browser / Android wrapper ── HTTPS + WebSocket ── Cloudflare Quick Tunnel
                                                           │
                                                           ▼
                                                    Local R.I.F.T. server
                                                     │              │
                                          local WebSocket       AWS services
                                                     │       IoT Core / SQS /
                                                     ▼       DynamoDB / S3
                                        Windows agents A…N ──────┘
```

- The server binds to `127.0.0.1:8765`. The PowerShell script starts the server and the Quick Tunnel.
- Agents connect outbound to the hub using `RIFT_SERVER_URL`; they can run on other computers and networks. The default is `http://127.0.0.1:8765` for a same-computer setup. Configure `RIFT_IOT_CERT`, `RIFT_IOT_PRIVATE_KEY`, and `RIFT_IOT_ROOT_CA` to select that computer's AWS IoT credentials.
- Each agent gets a stable internal ID on first run in `%LOCALAPPDATA%\RIFT\device.json`. The UI shows the computer's Windows hostname as its friendly name; set `RIFT_DEVICE_NAME` before starting an agent to give it a clearer per-device name. The ID is still used internally for routing and is hidden from the device list.
- The PWA uses `/api/devices` and device-tagged WebSocket events. The hub keeps connection and controller state per computer; it routes screen frames and input only for the selected device.
- The shared SQS queue already distributes compute chunks among all running agents as competing workers. Jobs are currently fleet-wide; pinned-to-device scheduling is not implemented.
- AWS IoT Core carries signed worker commands and results. DynamoDB stores device/job state; SQS queues compute chunks; S3 stores job outputs and viewer artifacts.
- The web app is served from `pwa/`; use its HTTPS tunnel URL rather than opening `index.html` directly. Camera access requires a secure browser context.

## Requirements

- Windows for the worker agent and remote desktop control.
- Python and pip. Install the project's Python dependencies from `agent/requirements.txt`.
- AWS CLI authenticated to the project's AWS account, plus a per-device AWS IoT certificate and the AWS resources configured for this project. The default profile name is `rift-admin`; set `RIFT_AWS_PROFILE` (or `AWS_PROFILE`) to use another local profile name. Resource names and the selected Region are still configured for this project.
- `cloudflared` installed and available on `PATH` for public cross-network testing.
- Ollama and the `qwen2.5:3b` model for the LLM tab only.
- Android Studio only if building/running the native wrapper.

## Run locally and across networks (Windows)

Open PowerShell in the project root. The server and agent use the same Python environment. Install the dependencies once:

```powershell
python -m pip install -r .\agent\requirements.txt
```

Start the tunnel/server in one PowerShell terminal:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_public_test.ps1
```

The script checks that `cloudflared` is installed, starts the server on port `8765`, waits for `/api/status`, and prints a temporary `https://…trycloudflare.com` URL. Keep this terminal open; press **Ctrl+C** to stop the tunnel and local server. A new tunnel URL is created after restart.

In a second PowerShell terminal, from the project root and using the same Python environment, start the Windows agent:

```powershell
python .\agent\rift_agent.py
```

For an agent on another computer/network, start the tunnel first, then start the agent from a project copy on that computer and point it at the printed HTTPS URL:

```powershell
$env:RIFT_SERVER_URL = 'https://your-current-name.trycloudflare.com'
$env:RIFT_DEVICE_NAME = 'Living Room PC'
python .\agent\rift_agent.py
```

Set a different `RIFT_DEVICE_NAME` for each computer (for example, `Office Laptop` or `Gaming PC`). That computer needs the Python dependencies, an AWS CLI profile for a team member invited to your AWS project, and its own AWS IoT certificate permitted to publish and subscribe only to that device's topics. Invite the friend as a team member in AWS Settings, then on their computer sign in to the same project using `aws login --profile rift-admin --region ap-southeast-2` (or use a different profile name and set `RIFT_AWS_PROFILE`). Provision a separate certificate for the friend's device in the project's selected Region; do not copy another device's private key. The Quick Tunnel URL changes when restarted, so remote agents must be restarted with the new URL; a stable named tunnel or hosted hub is the next deployment step.

The agent prints a six-digit Screen Share PIN. Keep the agent running in the logged-in Windows desktop session. To use the LLM tab, start Ollama separately and make sure `qwen2.5:3b` is available:

```powershell
ollama pull qwen2.5:3b
```

Then:

1. Open the printed HTTPS URL in Chrome on the phone.
2. Open the same URL on the laptop with `?view=laptop` for the laptop console (for example, `https://example.trycloudflare.com/?view=laptop`). Open it without the query on a phone for the mobile interface.
3. For screen share, open **Screen Share** on the phone, enter the PIN printed by the agent, and connect.
4. To browse files or upload from the phone, pair to the target computer in **Screen Share**, then open **Files**. Place files to share in `%USERPROFILE%\RIFT Shared`; phone uploads arrive under `%USERPROFILE%\RIFT Inbox`. Risky file types go under `%USERPROFILE%\RIFT Inbox\Quarantine`. The agent creates these folders on first start. Set `RIFT_FILES_ROOT` or `RIFT_INBOX_DIR` before starting the agent to change the locations. If the agent is on another computer/network, set `RIFT_SERVER_URL` to the current tunnel URL before launching it.
5. To stop, press **Stop** in the phone interface, then Ctrl+C in the tunnel terminal and stop the agent with Ctrl+C.

The AWS CLI login may need to be renewed. If required, run:

```powershell
aws login --region ap-southeast-2 --profile rift-admin
```

The server exits during startup if it cannot create its AWS session. If the project uses a different profile or Region, update the server and agent configuration consistently before running; changing only `AWS_DEFAULT_REGION` does not override the server's fixed Region.

## Android app (optional)

In Android Studio, open the `mobile-android` directory and run the `app` configuration on a device. On first launch, paste the active HTTPS tunnel URL; use **Set tunnel URL** after restarting the tunnel because Quick Tunnel URLs change. Camera use requires Android's camera permission. File selection, downloads, and fullscreen video are handled by the wrapper; other capabilities come from the same PWA as the mobile browser.

To build a debug APK from PowerShell:

```powershell
cd .\mobile-android
.\gradlew.bat :app:assembleDebug
```

The APK is written to `mobile-android\app\build\outputs\apk\debug\app-debug.apk`. Build output and Gradle caches are generated files, not source.

`mobile-android/serve_mobile.py` is an older static HTTP helper for same-network page previews. It does not start the API/WebSocket backend and does not provide HTTPS camera access; use the Quick Tunnel workflow above for integrated testing.

## Folder structure

```text
.
├── agent/                         Windows worker and desktop-control relay
│   ├── comms/                     AWS IoT MQTT connection
│   ├── security/                  HMAC verification and command allowlist
│   ├── workers/                   Math, image-tile, and local LLM workers
│   ├── vision/                    OpenCV face detector
│   ├── certs/                     Local AWS IoT certificates and policy files
│   ├── identity.py                Stable per-install computer identity
│   ├── files_service.py           Root-confined shared-folder and inbox operations
│   ├── config.py                  Hub URL, AWS, queue, storage, and model config
│   ├── requirements.txt           Python dependencies
│   ├── rift_agent.py              Agent lifecycle, telemetry, job polling, screen relay
│   └── test_agent.py              Agent/component verification script
├── backend/
│   └── orchestrator/              Standalone job splitter/reducer prototype and test
├── docs/                          Setup, AWS, Android, operations, and troubleshooting guides
├── infra/                         Reserved for infrastructure-as-code (currently empty)
├── mobile-android/                Android WebView wrapper and legacy static helper
│   ├── app/src/main/               Manifest, Java activity, resources, web assets
│   ├── gradle/wrapper/              Gradle wrapper files
│   └── serve_mobile.py              Legacy static-only HTTP server
├── pwa/
│   ├── index.html                  Browser UI, laptop console, screen-share controls
│   └── manifest.json               Installable web-app metadata
├── scripts/
│   └── start_public_test.ps1       Starts local server and Cloudflare Quick Tunnel
├── server/
│   ├── hub/
│   │   └── device_registry.py      Per-device sockets, presence, controllers, and SQLite history
│   └── rift_server.py              aiohttp API, WebSocket routing, AWS orchestration
├── AGENTS.md                       Codex/project development instructions
├── CLAUDE.md                       Claude/project development instructions
└── README.md                       Project overview and run guide
```

Generated directories such as `__pycache__/`, `mobile-android/.gradle/`, and `mobile-android/**/build/` are intentionally omitted.

## Useful documentation

- [Android app guide](docs/ANDROID_APP_GUIDE.md)
- [Operations manual](docs/OPERATIONS_MANUAL.md)
- [AWS setup guide](docs/AWS_ACCOUNT_SETUP_GUIDE.md)
- [Setup troubleshooting](docs/setup-troubleshooting.md)

The operations guide contains older implementation notes in places. For current run commands and the current source layout, use this README and the code.

## Development notes and limitations

- The Quick Tunnel is temporary, and its URL changes when restarted. Anyone who obtains the URL can reach the test server. Keep it private and stop it when testing is done.
- The app has no general user sign-in and uses permissive CORS. File API requests require a short-lived files token issued after Screen Share PIN pairing with that computer; signed file links expire after ten minutes. Other API routes and pages do not have general user authentication. Treat the tunnel as publicly reachable during its lifetime.
- Screen-control pairing uses a six-digit PIN and currently allows one controlling phone. The agent must run in the interactive Windows desktop session; it cannot control the lock screen or secure desktop. Games may ignore injected keyboard input, and elevated games may require the agent to run at the same privilege level.
- Files access is available only to the phone currently paired to that computer for Screen Share. Pairing grants a temporary files token, and the hub relays file data over the agent control WebSocket in bounded 512 KB pieces; heavy transfers can compete with screen frames. Signed links expire after ten minutes. Browser-supported images, PDFs, text, MP4, and WebM can be previewed; playback depends on the browser's codec support. FFmpeg transcoding, upload approval workflows, per-user invites, and view-only grants are not implemented. Keep the URL private and test with non-sensitive files.
- The hub supports multiple devices for presence, telemetry, ping routing, and screen control. Authentication/enrollment, phone-to-device access permissions, view-only watchers, per-device AWS IoT provisioning, pinned jobs, and a plugin loader remain future work. The backend/orchestrator directory is a separate prototype, not the server's active API implementation.
- `server/rift_server.py` contains development defaults, including the `rift-admin` profile, Region/resource names, and HMAC secret. Replace these with environment-specific configuration and rotate credentials/secrets before any deployment beyond private testing.
- Keep `agent/certs/device.private.key` and other device credentials private. Do not commit or share private keys, AWS credentials, public tunnel URLs, or the screen-share PIN.

## Tests and checks

The repository includes `agent/test_agent.py` and `backend/orchestrator/test_orchestrator.py`. Review their contents before running them: some checks make live AWS calls, enqueue jobs, or write test records. Syntax-only checks can be run without contacting AWS:

```powershell
python -m py_compile .\server\rift_server.py .\agent\rift_agent.py
```
