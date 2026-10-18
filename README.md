# R.I.F.T.

R.I.F.T. is a development-stage remote command center for one or more computers. Phone and laptop browsers connect to a hub; Windows agents provide telemetry, compute work, and consent-based remote screen control. The server handles the web API and phone-camera processing. AWS services coordinate worker commands and distributed jobs. A single EC2 hub is deployed in `ap-southeast-2`; local development with a temporary Cloudflare Tunnel remains available.

> **Development project:** local defaults in the source are for one test setup, including a development HMAC secret. The public Quick Tunnel and hosted test hub allow unauthenticated API and WebSocket access to anyone who can reach their URLs. The hub still uses the shared HMAC secret for signed AWS IoT commands and a dedicated hub IoT listener certificate. The hosted test hub's runtime IAM policy is broader than least privilege; see [the EC2 migration guide](infra/README.md) before production use.

## Features

| Area | What it does |
| --- | --- |
| **Fleet** | Lists all computers connected to the hub, with stable IDs, capabilities, telemetry, online state, and current controller. Known devices are retained in a local SQLite registry. |
| **Laptop console** | Opens with `?view=laptop`. Shows connected mobile clients, lets the laptop ping them, displays the phone camera preview, shows remote-control status, and lists compute jobs. |
| **Compute** | Submits chunked Monte Carlo, matrix multiplication, or image-tile jobs. The server records jobs/chunks in DynamoDB and enqueues chunks in SQS; the agent polls for work, executes it on CPU, and stores results for aggregation. |
| **Vision** | Captures frames from the phone's rear camera in the browser, sends them to the server over HTTPS, and runs OpenCV Haar-cascade face detection. Detection results and a camera preview are sent to the laptop console. This is face detection, not general-purpose object recognition. |
| **LLM** | Sends prompts through the hub to a selected online computer agent. That computer runs Ollama locally with `qwen2.5:3b` by default and streams generated tokens back through AWS IoT. The EC2 hub does not need to run a model. |
| **Screen Share** | Selects an available computer and lets one phone pick it up until it drops the session or disconnects. Streams at up to 1280×720 (about five frames per second) and relays mouse and virtual-keyboard input. |
| **Files (development)** | The phone and computer browse, upload, and download from the same `RIFT Shared` folder. Uploads are chunked and can resume in the same browser session. Risky executable/script extensions are quarantined under `RIFT Shared\Quarantine`; R.I.F.T. never runs uploaded files. |
| **Cross-network access** | Development uses a temporary Cloudflare Quick Tunnel. The hosted test hub uses a stable CloudFront HTTPS/WSS endpoint for the phone PWA and computer agents. Worker agents connect outbound to AWS IoT Core over TLS, so devices do not need to share Wi-Fi. |
| **Android wrapper** | A native Android WebView shell opens the same HTTPS PWA and supports camera permission requests, file selection/upload, downloads, and fullscreen video. The browser PWA is the simplest way to test features. |

## Architecture

```text
Development: Phone / Laptop / Agents ── HTTPS + WSS ── Cloudflare Quick Tunnel ── Local hub
                                                                                │
Hosted:      Phone / Laptop / Agents ── HTTPS + WSS ── CloudFront ── Nginx ─────┤
                                                                        EC2 Hub │
                                                                                ├─ agent WebSockets
                                                                                └─ AWS IoT / SQS / DynamoDB / S3
```

- For local development, the server binds to `127.0.0.1:8765`; the PowerShell script starts it and a temporary Quick Tunnel. The deployed EC2 configuration keeps aiohttp loopback-only behind Nginx and CloudFront.
- Agents connect outbound to the hub using `RIFT_SERVER_URL`; they can run on other computers and networks. The default is `http://127.0.0.1:8765` for a same-computer setup. Configure `RIFT_IOT_CERT`, `RIFT_IOT_PRIVATE_KEY`, and `RIFT_IOT_ROOT_CA` to select that computer's AWS IoT credentials.
- Each agent gets a stable internal ID on first run in `%LOCALAPPDATA%\RIFT\device.json`. The UI shows the computer's Windows hostname as its friendly name; set `RIFT_DEVICE_NAME` before starting an agent to give it a clearer per-device name. The ID is still used internally for routing and is hidden from the device list.
- The PWA uses `/api/devices` and device-tagged WebSocket events. The hub keeps connection and controller state per computer; it routes screen frames and input only for the selected device.
- The shared SQS queue already distributes compute chunks among all running agents as competing workers. Jobs are currently fleet-wide; pinned-to-device scheduling is not implemented.
- AWS IoT Core carries signed worker commands and results. DynamoDB stores device/job state; SQS queues compute chunks; S3 stores job outputs and viewer artifacts.
- The web app is served from `pwa/`; use its HTTPS tunnel URL rather than opening `index.html` directly. Camera access requires a secure browser context.

## Hosted test hub

Current test URL: **https://d2nv2vasdntzxl.cloudfront.net**. The endpoint is stable while the `rift-hub-sydney` stack exists. Open this URL in Chrome on the phone. Open `https://d2nv2vasdntzxl.cloudfront.net/?view=laptop` on the laptop for the laptop console. The test hub's APIs and WebSockets do not require a hub access token.

For your own Windows agent, make sure `config/local.ps1` has this computer's AWS profile and IoT certificate paths, then run:

```powershell
if (-not (Test-Path .\config\local.ps1)) { Copy-Item .\config\local.example.ps1 .\config\local.ps1; notepad .\config\local.ps1 }
& .\scripts\start_hosted_agent.ps1 -Profile rift-admin -DeviceName 'My Laptop'
```

To connect a teammate's computer, invite them to the AWS project and have them sign in using their own profile. They need their own IoT certificate/policy, then copy `config/local.example.ps1` to `config/local.ps1` and fill in their device ID, name, and certificate paths. Send the shared HMAC secret privately; they should save it in `config/.secrets/hub/agent-connection.env` as `RIFT_HMAC_SECRET=...`, then run:

```powershell
& .\scripts\start_hosted_agent.ps1 -Profile rift-admin -SecretsFile config/.secrets/hub/agent-connection.env -DeviceName 'Friend PC' -DeviceId 'friend-pc-01'
```

Do not share AWS credentials or device private keys. The hosted-agent script validates the AWS project, loads secrets without printing them, and connects to the CloudFront hub. The configured $30 monthly project-wide budget alert is an email notification, not a spending cap.

## Requirements

- Windows for the worker agent and remote desktop control.
- Python and pip. Install the project's Python dependencies from `agent/requirements.txt`.
- AWS CLI authenticated to the project's AWS account, plus a per-device AWS IoT certificate and the AWS resources configured for this project. The default profile name is `rift-admin`; set `RIFT_AWS_PROFILE` (or `AWS_PROFILE`) to use another local profile name. Resource names and the selected Region are still configured for this project.
- `cloudflared` installed and available on `PATH` only when running the local public-test tunnel.
- Ollama and the `qwen2.5:3b` model on each computer that should provide the LLM capability.
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

For an agent on another computer/network, start the tunnel first, then start the agent from a project copy on that computer and point it at the printed HTTPS URL (or use the Hosted test hub instructions above):

```powershell
$env:RIFT_SERVER_URL = 'https://your-current-name.trycloudflare.com'
$env:RIFT_DEVICE_NAME = 'Living Room PC'
python .\agent\rift_agent.py
```

Set a different `RIFT_DEVICE_NAME` and unique `RIFT_DEVICE_ID` for each computer (for example, `Gaming PC` and `friend-pc-01`). That computer needs the Python dependencies, an AWS CLI profile for a team member invited to your AWS project, and its own AWS IoT certificate. Invite the friend as a team member in AWS Settings, then sign in on their computer to the same project using `aws login --profile rift-admin --region ap-southeast-2` (or use a different local profile name and set `RIFT_AWS_PROFILE`). Create a separate device certificate and attach an IoT policy scoped to its client ID/topics; the reusable policy template is `agent/certs/rift_device_policy.template.json`. Do not copy another device's private key. The hub and agent must also use the same `RIFT_HMAC_SECRET`; set it in the hub's PowerShell before starting the public-test script and in the agent's PowerShell before starting that agent. Keep this value out of GitHub and transfer it privately. For cross-network use of the deployed hub, follow the Hosted test hub instructions above instead of using the changing local Quick Tunnel URL.

Keep the agent running in the logged-in Windows desktop session. To provide LLM requests from that computer, install/start Ollama there and download `qwen2.5:3b`:

```powershell
ollama pull qwen2.5:3b
```

Ollama's default local API is `http://127.0.0.1:11434` (note the five-digit port). The agent checks that Ollama and the model are available when it connects; if you start Ollama or pull the model after the agent, restart the agent so the computer advertises the LLM capability. The phone's LLM tab lets you select among connected computers that advertise it.

Then:

1. Open the printed HTTPS URL in Chrome on the phone.
2. Open the same URL on the laptop with `?view=laptop` for the laptop console (for example, `https://example.trycloudflare.com/?view=laptop`). Open it without the query on a phone for the mobile interface.
3. For screen share, open **Screen Share** on the phone, select an available computer, and tap **Pick up**. Only one phone can control a computer at a time; tap **Drop** when finished.
4. To browse, upload, or download files, pick up the target computer, then open **Files**. The phone and computer use `%USERPROFILE%\RIFT Shared` as their common folder. Risky file types go under `%USERPROFILE%\RIFT Shared\Quarantine`. The agent creates the shared folder on first start. Set `RIFT_FILES_ROOT` before starting the agent to change its location. For a computer on another network, use the hosted URL shown under Hosted test hub, or the current tunnel URL for local testing.
5. To stop, press **Stop** in the phone interface, then Ctrl+C in the tunnel terminal and stop the agent with Ctrl+C.

The AWS CLI login may need to be renewed. If required, run:

```powershell
aws login --region ap-southeast-2 --profile rift-admin
```

The server exits during startup if it cannot create its AWS session. If the project uses a different profile or Region, update the server and agent configuration consistently before running; changing only `AWS_DEFAULT_REGION` does not override the server's fixed Region.

## Android app (optional)

In Android Studio, open the `mobile-android` directory and run the `app` configuration on a device. On first launch, use the hosted HTTPS URL above or the active local tunnel URL; the tunnel URL changes each time local tunneling restarts. Camera use requires Android's camera permission. File selection, downloads, and fullscreen video are handled by the wrapper; other capabilities come from the same PWA as the mobile browser.

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
│   ├── files_service.py           Root-confined shared-folder upload/download operations
│   ├── config.py                  Hub URL, AWS, queue, storage, and model config
│   ├── requirements.txt           Python dependencies
│   ├── rift_agent.py              Agent lifecycle, telemetry, job polling, screen relay
│   └── test_agent.py              Agent/component verification script
├── backend/
│   └── orchestrator/              Standalone job splitter/reducer prototype and test
├── config/
│   ├── local.example.ps1          Per-computer AWS profile and IoT certificate paths
│   └── .secrets/                  Ignored local hub/device credentials
├── docs/                          Setup, AWS, Android, operations, and troubleshooting guides
├── infra/ec2/                     Single-instance CloudFormation, systemd, Nginx, and production env example
├── mobile-android/                Android WebView wrapper and legacy static helper
│   ├── app/src/main/               Manifest, Java activity, resources, web assets
│   ├── gradle/wrapper/              Gradle wrapper files
│   └── serve_mobile.py              Legacy static-only HTTP server
├── pwa/
│   ├── index.html                  Browser UI, laptop console, screen-share controls
│   └── manifest.json               Installable web-app metadata
├── scripts/
│   ├── start_public_test.ps1       Starts local server and Cloudflare Quick Tunnel
│   ├── start_hosted_agent.ps1      Loads private settings and joins the hosted hub
│   ├── install_hub_secrets.ps1     Installs hub secrets through SSM and temporary encrypted S3
│   ├── deploy_hub.ps1              Packages and deploys the single EC2 hub
│   ├── provision_hub_iot_certificate.ps1 Creates the dedicated hub MQTT certificate
│   ├── create_hub_budget.ps1       Creates the project budget alert
│   └── smoke_public_hub.py         Checks auth, presence, ping, screen and input routing
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
- The test hub has no general sign-in: its APIs and WebSockets are publicly reachable, while production CORS is same-origin by default. Screen Share pickup does not require a PIN; anyone who can access the public hub can try to claim an unclaimed computer. Only one phone holds a computer lease at a time. File API requests require the current controller lease and its temporary file token; signed file links expire after ten minutes.
- The agent must run in the interactive Windows desktop session; it cannot control the lock screen or secure desktop. Games may ignore injected keyboard input, and elevated games may require the agent to run at the same privilege level.
- Files access is available only to the phone currently controlling that computer. The hub relays file data over the agent control WebSocket in bounded 512 KB pieces; heavy transfers can compete with screen frames. The common folder is `%USERPROFILE%\RIFT Shared`; uploads appear there, except risky extensions quarantined in its `Quarantine` subfolder. Signed links expire after ten minutes. Browser-supported images, PDFs, text, MP4, and WebM can be previewed; playback depends on the browser's codec support. FFmpeg transcoding, upload approval workflows, per-user invites, and view-only grants are not implemented. Keep the URL private and test with non-sensitive files.
- The hub supports multiple devices for presence, telemetry, ping routing, and screen control. Authentication/enrollment, phone-to-device access permissions, view-only watchers, per-device AWS IoT provisioning, pinned jobs, and a plugin loader remain future work. The backend/orchestrator directory is a separate prototype, not the server's active API implementation.
- `server/rift_server.py` retains local development defaults but reads AWS resource names, Region, model, and secrets from environment variables. Production mode requires a shared HMAC secret and a dedicated hub IoT certificate. The hub dispatches LLM requests to an online agent instead of trying to run Ollama on EC2. See [the EC2 migration guide](infra/README.md) before using the deployment artifacts.
- Keep `agent/certs/device.private.key` and other device credentials private. Do not commit or share private keys, AWS credentials, or public tunnel URLs.

## Tests and checks

The repository includes `agent/test_agent.py` and `backend/orchestrator/test_orchestrator.py`. Review their contents before running them: some checks make live AWS calls, enqueue jobs, or write test records. Syntax-only checks can be run without contacting AWS:

```powershell
python -m py_compile .\server\rift_server.py .\agent\rift_agent.py
```
