# SwarmSight — Deployment Plan (10-Hour Build)

> **Read-only document.** No AWS resources exist yet. This plan documents
> every step required to go from zero to a running demo. Follow in sequence.
> Time estimates assume no major blockers — adjust based on your pace.

---

## Pre-flight Checklist (Before Starting the Clock)

- [ ] AWS CLI installed and configured (`aws configure --profile <profile>`)
- [ ] `python3 --version` >= 3.10 available locally
- [ ] SSH key pair available (or plan to create one in Step 0)
- [ ] Two demo MP4 clips ready (or know where to download crowd footage)
- [ ] Commander SMS number and email address ready for SNS subscription

---

## Phase 0 — AWS Foundation (Target: 00:00–00:30)

**Goal: All five AWS resources exist and are reachable from each other.**

### 0.1 — S3 Bucket

```bash
aws s3api create-bucket \
  --bucket swarmsight-demo-kesha \
  --region ap-south-1 \
  --create-bucket-configuration LocationConstraint=ap-south-1

# Block all public access
aws s3api put-public-access-block \
  --bucket swarmsight-demo-kesha \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# Enable SSE-S3 encryption
aws s3api put-bucket-encryption \
  --bucket swarmsight-demo-kesha \
  --server-side-encryption-configuration '{
    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
  }'

# Upload demo clips
aws s3 cp normal-concert.mp4 s3://swarmsight-demo-kesha/clips/
aws s3 cp compression-precursor.mp4 s3://swarmsight-demo-kesha/clips/
```

Checkpoint: `aws s3 ls s3://swarmsight-demo-kesha/clips/` shows both files.

---

### 0.2 — IAM Role and Instance Profile

```bash
# Create the role with EC2 trust policy
aws iam create-role \
  --role-name swarmsight-ec2-role \
  --assume-role-policy-document file://docs/trust-policy.json

# Attach the inline application policy (see iam-policy-design.md for JSON)
aws iam put-role-policy \
  --role-name swarmsight-ec2-role \
  --policy-name swarmsight-app-policy \
  --policy-document file://docs/app-policy.json

# Create instance profile and attach role
aws iam create-instance-profile --instance-profile-name swarmsight-ec2-profile
aws iam add-role-to-instance-profile \
  --instance-profile-name swarmsight-ec2-profile \
  --role-name swarmsight-ec2-role
```

Files to create locally (not on EC2):
- `docs/trust-policy.json` — the trust policy from iam-policy-design.md
- `docs/app-policy.json` — the clean inline policy JSON from iam-policy-design.md

Checkpoint: `aws iam get-instance-profile --instance-profile-name swarmsight-ec2-profile`
shows the role attached.

---

### 0.3 — Security Group

```bash
# Get your public IP
MY_IP=$(curl -s https://checkip.amazonaws.com)/32
echo "My IP: $MY_IP"

# Create security group (get default VPC ID first)
VPC_ID=$(aws ec2 describe-vpcs \
  --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text --region ap-south-1)

SG_ID=$(aws ec2 create-security-group \
  --group-name swarmsight-sg \
  --description "SwarmSight demo security group" \
  --vpc-id $VPC_ID \
  --region ap-south-1 \
  --query GroupId --output text)

# SSH inbound (your IP only)
aws ec2 authorize-security-group-ingress \
  --group-id $SG_ID \
  --protocol tcp --port 22 --cidr $MY_IP --region ap-south-1

# App inbound (your IP only)
aws ec2 authorize-security-group-ingress \
  --group-id $SG_ID \
  --protocol tcp --port 8000 --cidr $MY_IP --region ap-south-1
```

Checkpoint: `aws ec2 describe-security-groups --group-ids $SG_ID --region ap-south-1`
shows two inbound rules, both scoped to your IP.

---

### 0.4 — EC2 Instance

```bash
# Get latest Ubuntu 22.04 LTS AMI in ap-south-1
AMI_ID=$(aws ec2 describe-images \
  --owners 099720109477 \
  --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
            "Name=state,Values=available" \
  --query 'sort_by(Images,&CreationDate)[-1].ImageId' \
  --output text --region ap-south-1)

# Launch instance (replace YOUR_KEY_PAIR_NAME)
INSTANCE_ID=$(aws ec2 run-instances \
  --image-id $AMI_ID \
  --instance-type t3.large \
  --key-name YOUR_KEY_PAIR_NAME \
  --security-group-ids $SG_ID \
  --iam-instance-profile Name=swarmsight-ec2-profile \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":30,"VolumeType":"gp3"}}]' \
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=swarmsight-demo}]' \
  --region ap-south-1 \
  --query 'Instances[0].InstanceId' --output text)

echo "Instance ID: $INSTANCE_ID"

# Wait for running state (~60s)
aws ec2 wait instance-running --instance-ids $INSTANCE_ID --region ap-south-1

# Get public IP
PUBLIC_IP=$(aws ec2 describe-instances \
  --instance-ids $INSTANCE_ID \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text --region ap-south-1)
echo "SSH: ssh -i YOUR_KEY.pem ubuntu@$PUBLIC_IP"
```

Checkpoint: `ssh -i YOUR_KEY.pem ubuntu@$PUBLIC_IP` connects successfully.

---

### 0.5 — DynamoDB Table

```bash
aws dynamodb create-table \
  --table-name swarmsight_alerts \
  --attribute-definitions \
    AttributeName=zone_id,AttributeType=S \
    AttributeName=timestamp,AttributeType=N \
  --key-schema \
    AttributeName=zone_id,KeyType=HASH \
    AttributeName=timestamp,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --region ap-south-1

# Enable TTL on the ttl attribute
aws dynamodb update-time-to-live \
  --table-name swarmsight_alerts \
  --time-to-live-specification "Enabled=true,AttributeName=ttl" \
  --region ap-south-1
```

Checkpoint: `aws dynamodb describe-table --table-name swarmsight_alerts --region ap-south-1`
shows status ACTIVE.

---

### 0.6 — SNS Topic and Subscriptions

```bash
# Create topic
TOPIC_ARN=$(aws sns create-topic \
  --name swarmsight-alerts \
  --attributes DisplayName=SwarmSightAlert \
  --region ap-south-1 \
  --query TopicArn --output text)
echo "Topic ARN: $TOPIC_ARN"

# Subscribe commander email (replace with real address)
aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol email \
  --notification-endpoint commander@example.com \
  --region ap-south-1

# Subscribe commander SMS (replace with real number in E.164 format)
aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol sms \
  --notification-endpoint +919XXXXXXXXX \
  --region ap-south-1
```

CRITICAL: Check your email and confirm the subscription NOW.
Do not proceed to Step 1 until the email subscription shows "Confirmed"
in the SNS console. Unconfirmed subscriptions receive nothing.

Checkpoint: `aws sns list-subscriptions-by-topic --topic-arn $TOPIC_ARN --region ap-south-1`
shows both subscriptions with SubscriptionArn (not "PendingConfirmation").

---

### 0.7 — IAM Role Verification (from inside EC2)

```bash
# SSH into the instance and run:
aws sts get-caller-identity                        # must show swarmsight-ec2-role
aws s3 ls s3://swarmsight-demo-kesha/clips/        # must list clips
aws dynamodb list-tables --region ap-south-1       # must show swarmsight_alerts
aws sns list-topics --region ap-south-1            # must show swarmsight-alerts topic
aws ec2 describe-instances --region ap-south-1     # must FAIL (UnauthorizedOperation)
```

All four positives pass + negative fails = IAM role is correct.
Proceed to Step 1 only after this verification passes.

---

## Phase 1 — Backend Skeleton + Video Feed (Target: 00:30–02:00)

**Goal: Browser shows live video stream from EC2.**

### 1.1 — Instance Setup

```bash
# On EC2:
sudo apt-get update -y
sudo apt-get install -y python3.11 python3-pip python3-venv ffmpeg

python3 -m venv /opt/swarmsight
source /opt/swarmsight/bin/activate

pip install fastapi uvicorn[standard] opencv-python-headless boto3 numpy
```

### 1.2 — Project Layout

```
/opt/swarmsight/
├── app/
│   ├── main.py          <- FastAPI app, WebSocket, static mount
│   ├── feed.py          <- video reader, frame broadcaster
│   ├── density.py       <- CSRNet inference wrapper
│   ├── flow.py          <- Farneback optical flow + variance
│   ├── risk.py          <- rule-based risk fusion engine
│   ├── alerts.py        <- DynamoDB write + SNS publish
│   └── static/
│       ├── index.html
│       └── main.js
└── clips/               <- local fallback if not streaming from S3
    ├── normal-concert.mp4
    └── compression-precursor.mp4
```

### 1.3 — Start Server

```bash
cd /opt/swarmsight
source bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Checkpoint: `http://<EC2_IP>:8000` in browser shows video playing.

---

## Phase 2 — Density Model (Target: 02:00–04:00)

**Goal: Live heatmap overlay on video.**

### 2.1 — Download CSRNet Weights

```bash
# Option A: from a public checkpoint repo (e.g. github.com/leeyeehoo/CSRNet-pytorch)
# Option B: fallback to YOLOv8 nano if CSRNet does not integrate by 03:30
pip install ultralytics   # only if falling back to YOLO
```

### 2.2 — Density Grid Output

The density model outputs a 2D density map. Downsample to an 8x6 grid:

```python
import numpy as np
def to_grid(density_map, rows=6, cols=8):
    h, w = density_map.shape
    grid = density_map.reshape(rows, h//rows, cols, w//cols).mean(axis=(1,3))
    return grid  # shape: (6, 8)
```

Send the grid JSON alongside each frame over the WebSocket:

```json
{
  "frame": "<base64 JPEG>",
  "grid": [[0.1, 0.3, 2.1, ...], ...],
  "timestamp": 1726773600000
}
```

Checkpoint: Browser canvas shows colored grid overlay updating per frame.

---

## Phase 3 — Motion + Risk Fusion (Target: 04:00–05:30)

**Goal: Each cell shows Green/Yellow/Orange/Red based on density AND motion.**

### 3.1 — Risk Rule (encode exactly)

```python
RISK_THRESHOLDS = {
    "GREEN":  (0.0, 2.0),   # < 2 people/m2
    "YELLOW": (2.0, 3.5),   # 2-3.5 — Fruin LOS C/D
    "ORANGE": (3.5, 5.0),   # 3.5-5 — dangerous compression possible
    "RED":    (5.0, 999),   # > 5 — critical
}

def compute_risk(density, baseline_density, motion_variance,
                 baseline_variance, flow_coherence):
    # 1. Density band
    band = density_band(density)
    # 2. Micro-motion collapse signal (compression precursor)
    variance_drop = (baseline_variance - motion_variance) / (baseline_variance + 1e-6)
    compression_signal = (density > 2.5) and (variance_drop > 0.4)
    # 3. Involuntary wave signal
    wave_signal = (density > 2.0) and (flow_coherence > 0.75)
    # 4. Combined upgrade
    if compression_signal or wave_signal:
        band = escalate(band)  # e.g. YELLOW -> ORANGE, ORANGE -> RED
    return band
```

### 3.2 — Rolling Baseline

Collect the first 15 seconds of each clip (calibration window) to establish
per-zone baseline density and motion variance before risk scoring begins.

Checkpoint: Playing the normal-concert clip stays green throughout.
Playing the compression clip escalates to red.

---

## Phase 4 — Alerting (Target: 05:30–06:30)

**Goal: Orange/Red transitions write to DynamoDB and send real SMS/email.**

```python
import boto3, time, json

dynamodb = boto3.resource('dynamodb', region_name='ap-south-1')
sns = boto3.client('sns', region_name='ap-south-1')

TABLE = dynamodb.Table('swarmsight_alerts')
TOPIC_ARN = 'arn:aws:sns:ap-south-1:879268673611:swarmsight-alerts'

def on_alert(zone_id, risk_level, density, baseline, variance, coherence, reason, clip_name):
    ts = int(time.time() * 1000)
    TABLE.put_item(Item={
        'zone_id': zone_id,
        'timestamp': ts,
        'risk_level': risk_level,
        'density_value': str(density),
        'baseline_density': str(baseline),
        'motion_variance': str(variance),
        'flow_coherence': str(coherence),
        'reason': reason,
        'clip_name': clip_name,
        'ttl': int(time.time()) + 7 * 24 * 3600
    })
    if risk_level in ('ORANGE', 'RED'):
        sns.publish(
            TopicArn=TOPIC_ARN,
            Message=f"SwarmSight {risk_level} — {zone_id}: {reason}",
            Subject=f"SwarmSight Alert: {risk_level}"
        )
```

Checkpoint: Orange/Red transition → DynamoDB item appears + SMS/email received.

---

## Phase 5 — Dashboard Polish (Target: 06:30–08:00)

**Goal: Control-room-quality single page.**

Layout:
```
┌─────────────────────────┬───────────────────────────┐
│  Video + heatmap canvas │  Live alert feed          │
│  (left 60%)             │  (right 40%)              │
│                         │  ┌─────────────────────┐  │
│                         │  │ RED  grid_3_2 13:42 │  │
│                         │  │ Density 5.8/m2      │  │
│                         │  │ Baseline 2.1/m2     │  │
│                         │  │ Coherence 0.87      │  │
│                         │  │ Micro-motion ↓ 72%  │  │
│                         │  └─────────────────────┘  │
│                         │  Legend: G Y O R           │
└─────────────────────────┴───────────────────────────┘
```

Each alert card is expandable and shows the "why it fired" data —
this explainability panel is a named differentiator in the pitch.

---

## Phase 6 — Validation Run (Target: 08:00–09:00)

**Goal: Side-by-side demo of both clips with correct behavior.**

| Clip | Expected behavior |
|---|---|
| normal-concert.mp4 | Stays green throughout the calibration and playback period |
| compression-precursor.mp4 | Escalates to orange/red, DynamoDB write, SMS/email fires |

Run both clips end-to-end at least twice. Fix any flaky calibration
window behavior (hardcode baseline if needed — see build plan).

---

## Phase 7 — Buffer and Rehearsal (Target: 09:00–10:00)

- EC2 instance already running before judges arrive
- Browser tab already open on `http://<EC2_IP>:8000`
- Do not cold-start anything live during the demo
- Rehearse the narration 2+ times:
  *"This is a packed, stationary crowd enjoying a show — no alert. This
  is the same density, but micro-motion collapses and a pressure wave
  propagates — alert fires in real time."*

---

## Teardown (After Demo)

```bash
# Stop billing immediately after demo
aws ec2 terminate-instances --instance-ids $INSTANCE_ID --region ap-south-1

# Optional cleanup (keep for judges reviewing submission)
# aws s3 rb s3://swarmsight-demo-kesha --force
# aws dynamodb delete-table --table-name swarmsight_alerts --region ap-south-1
# aws sns delete-topic --topic-arn $TOPIC_ARN --region ap-south-1
# aws iam delete-role-policy --role-name swarmsight-ec2-role --policy-name swarmsight-app-policy
# aws iam remove-role-from-instance-profile --instance-profile-name swarmsight-ec2-profile --role-name swarmsight-ec2-role
# aws iam delete-instance-profile --instance-profile-name swarmsight-ec2-profile
# aws iam delete-role --role-name swarmsight-ec2-role
```

> Keep S3, DynamoDB, and SNS alive during judging so reviewers can
> verify the alert history in DynamoDB and see the bucket structure.
> Terminate EC2 only — it is the only resource with ongoing hourly cost.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| CSRNet weights fail to integrate by 03:30 | Medium | High | Fall back to YOLOv8 nano per-cell head count |
| EC2 instance too slow at 5 fps | Low | Medium | Reduce to 2 fps; reduce grid to 4x3 |
| SNS subscription not confirmed | Medium | High | Do this in Step 0, verify before Step 1 |
| Demo clip triggers false positive baseline | Medium | Medium | Hardcode baseline from known-good window |
| Security group IP changes during demo | Low | High | Use a hotspot with stable IP; or open to /0 temporarily (document it) |
| Judges need browser access from different IP | Low | Medium | Add their IP to security group inbound rule (port 8000 only) |
