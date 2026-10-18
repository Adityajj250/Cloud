# AWS EC2 hub migration

The first single-instance deployment is live in `ap-southeast-2`. The current endpoint and instance ID are available from the CloudFormation outputs for `rift-hub-sydney`; do not place the shared HMAC secret in this document. The migration was smoke-tested through the public CloudFront URL on 2026-10-18.

This is a single-hub design. The Python hub stays one process because its active device sockets, controller grants, and message routing are held in memory. The phone and computer agents connect to one shared HTTPS/WSS URL; laptop agents continue to execute compute work locally.

## What changed in the application

- Hub settings for Region, AWS profile, queue, bucket, DynamoDB table names, model, HMAC secret, server port, and hub MQTT certificate paths are now environment-configurable. In production, boto3 uses the EC2 instance role when no profile is set. Local development keeps the `rift-admin` profile default.
- `RIFT_ENV=production` requires a random `RIFT_HMAC_SECRET` of at least 32 characters. The hub does not require a shared access token for API or WebSocket connections. Existing WebSocket application message types remain unchanged.
- Production API calls and WebSockets are publicly reachable. `/healthz` is the health endpoint. Signed file-content links still use their existing short-lived signatures. CORS remains same-origin by default, which limits browser-origin access but does not authenticate non-browser clients.
- Production CORS is same-origin by default and can be constrained with `RIFT_ALLOWED_ORIGINS`. The CloudFront origin request policy forwards the viewer `Host` so the hub can validate its HTTPS origin.
- The EC2 hub must use a dedicated AWS IoT certificate for its MQTT result listener. Production refuses to reuse a computer agent certificate. The instance role supplies boto3 credentials; it does not replace the IoT MQTT certificate.
- SIGINT/SIGTERM now close WebSockets, stop the polling task, disconnect IoT MQTT, and clean up aiohttp. The systemd unit restarts the process after failures and sends logs to journald.

## Proposed topology

```text
Phone PWA ─┐
Laptop PWA ├─ HTTPS/WSS ─ CloudFront ─ HTTP:80 (CloudFront prefix list only) ─ Nginx ─ 127.0.0.1:8765 aiohttp hub
PC agents ─┘                                                                  │
                                                                              └─ EC2 instance role → existing DynamoDB/SQS/S3/IoT APIs
```

CloudFront provides a stable `https://<distribution>.cloudfront.net` browser/agent URL and WebSocket support. Viewer traffic is TLS; this starter topology uses HTTP from CloudFront to Nginx on the EC2 origin. The EC2 security group accepts that origin port only from CloudFront's managed origin-facing prefix list, and no SSH port is opened. Session Manager is the admin path. For end-to-end TLS to the origin, use a domain with a trusted origin certificate or a private CloudFront VPC origin after separately validating its region/plan eligibility and networking cost.

The test hub has no hub access token: anyone who can reach the public URL can call APIs or open a WebSocket. Screen Share pickup has no PIN; anyone who can reach the public URL may claim an unclaimed computer. Signed AWS IoT commands still use the shared HMAC secret. Treat this endpoint as a test service and avoid sensitive files or jobs.

LLM prompts are HMAC-signed and dispatched to the selected online computer through AWS IoT. Ollama and the configured model run on that computer; the EC2 hub does not need Ollama installed. Each agent advertises the LLM capability only when its local Ollama endpoint is reachable and the configured model is installed.

## IAM permissions and security posture

The EC2 instance role uses `AmazonSSMManagedInstanceCore` plus the IAM Policy Autopilot baseline `arn:aws:iam::903859732456:policy/rift-hub-runtime_1`. The generated policy was used for this test deployment after the owner approved it. It is broader than least privilege: it allows reads/writes across DynamoDB tables, S3 objects, SQS queues and IoT topics in the project scope, plus KMS operations through those services. It does not grant IAM administration. Replace it with a reviewed, resource-scoped policy before production use. Do not attach an administrator policy to the EC2 instance.

The hub is currently running with these production prerequisites installed:

1. A hub-specific AWS IoT certificate and private key, with a policy that permits the fixed hub MQTT client ID and subscription/receive on `rift/+/result` and `rift/+/tokens`.
2. A random shared HMAC secret. It must also be configured on every computer agent.
3. A package uploaded to the existing R.I.F.T. S3 bucket. The package script excludes local secrets, private keys, local AWS config, SQLite state, virtual environments, and build artifacts.

`HubRuntimePolicyArn` is a required template parameter. The existing S3 bucket and queue/table names are project-specific and must exist in the selected Region.

## Prepare and deploy

From the project root in PowerShell, after the AWS CLI profile is signed in:

```powershell
python .\scripts\package_hub.py
```

Inspect the archive's contents before upload. The deploy script defaults to a read-only plan and verifies the AWS identity and selected Region:

```powershell
.\scripts\deploy_hub.ps1 -Profile rift-admin
```

It does not upload or provision unless `-Apply` is supplied. Once the configuration, reviewed policy ARN, and required secrets are ready and you approve the AWS changes, deployment is:

```powershell
.\scripts\deploy_hub.ps1 -Apply -Profile rift-admin -HubRuntimePolicyArn 'arn:aws:iam::903859732456:policy/REVIEWED_RIFT_HUB_RUNTIME'
```

The script uploads the deployment ZIP to the existing artifact bucket and deploys `infra/ec2/hub-stack.yaml`. It creates one `t3.small` by default (or `t3a.small`), one 30 GiB encrypted gp3 volume, one Elastic IP, a security group, an EC2 instance role/profile, and one CloudFront distribution. It does **not** create a new DynamoDB/SQS/S3 application backend or the hub IoT certificate/policy.

The instance intentionally keeps the hub service stopped until its secrets and dedicated IoT certificate are installed. The deployment owner has already installed these files using the transfer helper below. For future secret rotation, generate a new local secret file and use the helper; do not paste secret values into SSM commands:

```powershell
python -c "import pathlib,secrets; p=pathlib.Path('config/.secrets/hub/hub-secrets.env'); p.parent.mkdir(parents=True,exist_ok=True); p.write_text('RIFT_HMAC_SECRET='+secrets.token_urlsafe(48)+'\\n',encoding='ascii')"
& .\scripts\install_hub_secrets.ps1 -InstanceId <INSTANCE_ID> -Profile rift-admin
```

The helper uploads a temporary AES-256 server-side-encrypted archive to the existing private S3 bucket, installs it through Systems Manager, restarts the service, and deletes the temporary object. Bucket versioning is disabled for this bucket. The local files in `config/.secrets/hub/` remain private and ignored by Git.

For troubleshooting, connect without SSH:

```powershell
aws ssm start-session --target <INSTANCE_ID> --profile rift-admin --region ap-southeast-2
```

`nano` runs inside the encrypted Session Manager terminal. Do not put certificate text or secrets in a command, source ZIP, EC2 user data, or Git. Verify service state/logs and the public health endpoint:

```bash
sudo systemctl status rift-hub nginx
sudo journalctl -u rift-hub -n 100 --no-pager
curl -fsS https://<distribution>.cloudfront.net/healthz
```

Set the same URL and secrets in each computer's PowerShell before running its agent:

```powershell
$env:RIFT_SERVER_URL = 'https://<distribution>.cloudfront.net'
$env:RIFT_HMAC_SECRET = '<shared HMAC secret from the hub owner>'
python .\agent\rift_agent.py
```

Open that URL in Chrome on the phone. Open `https://<distribution>.cloudfront.net/?view=laptop` on the laptop for the laptop dashboard. The phone and laptop no longer need to use the same Wi-Fi network.

For an application-only release, use the in-place Systems Manager updater. It updates `/opt/rift`, restarts the existing service, and does not modify or replace the EC2/CloudFront stack:

```powershell
.\scripts\update_hub_code.ps1 -Profile rift-admin
```

The updater prints the encrypted S3 package key it applied. To roll back, pass a previously retained package key:

```powershell
.\scripts\update_hub_code.ps1 -Profile rift-admin -ArtifactKey 'rift-deploy/rift-hub-update-YYYYMMDD-HHMMSS-XXXXXXXX.zip'
```

## Checks and rollback

- Health: `GET /healthz` should return `{"ok":true,...}` through the CloudFront URL.
- Public access: `/api/devices` and the WebSocket should connect without a hub access token. This is intentionally unauthenticated for the test deployment.
- Run the public end-to-end smoke test with `python .\scripts\smoke_public_hub.py --url https://<distribution>.cloudfront.net`. It checks HTTPS health, unauthenticated API/WebSocket access, agent registration, phone device discovery, control approval, screen-frame relay, and phone-to-agent keyboard input. It uses temporary test identities and disconnects them at the end.
- Registration/routing: start at least one agent with the CloudFront URL; it should appear in `/api/devices`. From the PWA, select it and tap **Pick up** to claim the computer, then test ping/control. Only one phone can hold a computer at a time; **Drop** releases it. Screen-share input/frame message formats are unchanged.
- Screen-share frames remain base64 JSON JPEGs captured by each computer agent at up to 1280×720 and about five frames per second. The hub relays them to that device's one active controller; the EC2 server does not capture the remote screen.
- A typical 100 KB JPEG at 5 fps is about 2.4 GB/hour after base64 overhead; 300 KB is about 7.2 GB/hour. That uses roughly 42 or 14 hours, respectively, of a 100 GB CloudFront Free plan transfer allowance. Measure average actual frame size in your test; motion/content changes compression size. Additional viewer traffic can exceed the free allowance/plan terms.
- To revert application code, upload a previously packaged ZIP and redeploy the stack with its S3 key, then restart the service using Session Manager. The current SQLite device history lives on the instance EBS volume; a stack replacement/termination loses it. The live registry and controller sessions always reset on hub restart.
- To stop compute billing temporarily, stop the EC2 instance; the public app is then unavailable and the Elastic IP remains billable. To remove the deployment, delete the CloudFormation stack after preserving any needed EBS state and artifacts. CloudFront deletion can take several minutes; the uploaded ZIP in the existing S3 bucket is not deleted by stack deletion.

## Cost and AWS project checks

- Read-only AWS identity check: project `903859732456`, assumed role `AccountFullAccessRole`; the simulator allowed EC2 launch, IAM role/pass-role, CloudFormation stack creation, Budgets creation, and CloudFront distribution creation in the checked contexts. Simulated permission does not guarantee that resource-specific conditions or service quotas will permit every final call.
- Confirmed selected Region: `ap-southeast-2`; one `t3.small` instance is running as part of this deployment. `t3.small` and `t3a.small` are offered in the inspected Sydney AZs. Standard on-demand vCPU quota reports 5 vCPUs, enough for a 2-vCPU instance if no concurrent quota use.
- The AWS project is on the Free plan and currently reports $100 of remaining plan credits through 2027-04-17. The API does not establish whether separate student credits exist; check AWS Settings → Billing. Current estimate uses full on-demand price and does not assume credits or EC2 free-tier eligibility.
- Estimated 730-hour base: `t3.small` about $19.27 + 30 GiB gp3 about $2.88 + public IPv4 about $3.65 = **about $25.80/month**, before transfer outside included CloudFront allowances, T3 surplus-credit charges, snapshots, or tax. `t3a.small` is about $17.37/month compute, reducing that estimate by about $1.90. No NAT Gateway, ALB, second EC2, or custom domain is included.
- Current CloudFront Free flat-rate plan advertises 1 million requests and 100 GB/month transfer with no overage fees; exceeding the allowance can require upgrading the plan, and the live AWS Settings plan/terms should be checked before enabling long sessions. The $30/month budget alert is configured with 80% actual and 100% forecast notifications; also review AWS Settings → Billing for the project's spend limit. A budget is an alert, not a hard spending cap.
- The configured USD $30 monthly project-wide budget alerts at 80% actual and 100% forecast spend. It includes other project services, not only R.I.F.T., and does not cap spending. To change its recipient or threshold, update `scripts/create_hub_budget.ps1` and review the existing budget first.
- Current live resources are the single EC2 instance, its encrypted EBS volume and Elastic IP, the security group and instance role/profile, and one CloudFront distribution, all created by the `rift-hub-sydney` stack. The $30 monthly budget alert is configured.
