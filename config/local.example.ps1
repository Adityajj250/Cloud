# Copy to config/local.ps1 and set values for this computer. This file contains
# no AWS keys, IoT private key, or shared HMAC secret.
$env:RIFT_AWS_PROFILE = 'rift-admin'
$env:RIFT_AWS_REGION = 'ap-southeast-2'
$env:AWS_DEFAULT_REGION = 'ap-southeast-2'
$env:RIFT_IOT_ENDPOINT = 'a30f3sdnp94spe-ats.iot.ap-southeast-2.amazonaws.com'
$env:RIFT_SQS_QUEUE_URL = 'https://sqs.ap-southeast-2.amazonaws.com/903859732456/rift-chunks-queue'
$env:RIFT_S3_BUCKET = 'rift-storage-903859732456-ap-southeast-2'

# Give every computer a unique ID and a human-friendly name.
$env:RIFT_DEVICE_ID = 'REPLACE_WITH_UNIQUE_DEVICE_ID'
$env:RIFT_DEVICE_NAME = 'REPLACE_WITH_DEVICE_NAME'

# Provision a separate AWS IoT certificate/key for this computer and store it
# under config/.secrets/<device-id>/. Never copy another computer's private key.
$env:RIFT_IOT_CERT = 'config/.secrets/REPLACE_WITH_UNIQUE_DEVICE_ID/agent.pem.crt'
$env:RIFT_IOT_PRIVATE_KEY = 'config/.secrets/REPLACE_WITH_UNIQUE_DEVICE_ID/agent.private.key'
$env:RIFT_IOT_ROOT_CA = 'agent/certs/AmazonRootCA1.pem'
$env:RIFT_OLLAMA_URL = 'http://127.0.0.1:11434'
$env:RIFT_LLM_MODEL = 'qwen2.5:3b'

# The hosted-agent launcher sets RIFT_SERVER_URL and RIFT_HMAC_SECRET from
# the separately transferred local secrets file. The public test hub does not
# require a hub access token.
