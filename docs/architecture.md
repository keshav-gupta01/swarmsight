# SwarmSight — AWS Architecture (10-Hour Demo Build)

> **Scope:** This document describes the minimal, single-EC2 architecture used
> for the 10-hour hackathon demo. It deliberately omits Kinesis Video Streams,
> SageMaker endpoints, Greengrass, Step Functions, Timestream, and Location
> Service — all of which appear in the full production design
> (`drone-crowd-safety-solution.md`). That scoping decision is intentional and
> should be stated explicitly in the pitch as engineering judgment.

---

## Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        DEMO ENVIRONMENT                              │
│                     AWS Account: 879268673611                        │
│                     Region: ap-south-1 (Mumbai)                      │
└──────────────────────────────────────────────────────────────────────┘

 Pre-recorded MP4 clips                 Commander laptop / browser
 (2 demo videos on EC2 or S3)                     │
          │                                        │ HTTP / WebSocket (ws://)
          │ read frame-by-frame                    │
          ▼                                        ▼
┌─────────────────────────────────────────────────────────┐
│                   EC2  t3.large                         │
│              (swarmsight-demo, ap-south-1a)             │
│                                                         │
│  ┌────────────────────────────────────────────────────┐ │
│  │  FastAPI application  (port 8000)                  │ │
│  │  ├── REST endpoints  /api/*                        │ │
│  │  ├── WebSocket       /ws/feed                      │ │
│  │  └── Static frontend /  (index.html + JS)          │ │
│  └────────────────────────────────────────────────────┘ │
│                                                         │
│  ┌─────────────────────┐  ┌───────────────────────────┐│
│  │  Crowd Density CNN  │  │  Optical Flow (OpenCV)    ││
│  │  (CSRNet pretrained)│  │  Farneback + grid variance││
│  └─────────────────────┘  └───────────────────────────┘│
│                                                         │
│  ┌─────────────────────────────────────────────────── ┐ │
│  │  Rule-based Risk Fusion                            │ │
│  │  (density threshold + micro-motion variance drop + │ │
│  │   flow coherence + per-zone rolling baseline)      │ │
│  └─────────────────────────────────────────────────── ┘ │
│                                                         │
│  IAM Instance Profile: swarmsight-ec2-role              │
└────────────────┬──────────────────┬─────────────────────┘
                 │                  │
         boto3 SDK calls      boto3 SDK calls
                 │                  │
    ┌────────────▼──┐    ┌──────────▼──────────────────┐
    │  S3 Bucket    │    │  DynamoDB Table              │
    │  swarmsight-  │    │  swarmsight_alerts           │
    │  demo-*       │    │  PK: zone_id (S)             │
    │               │    │  SK: timestamp (N, Unix ms)  │
    │  /clips/      │    │  attrs: density, coherence,  │
    │  /snapshots/  │    │         risk_level, reason   │
    └───────────────┘    └──────────────┬───────────────┘
                                        │ alert write triggers
                                        │ SNS publish (boto3)
                              ┌─────────▼──────────────┐
                              │  SNS Topic             │
                              │  swarmsight-alerts     │
                              │  Subscriptions:        │
                              │  • SMS  → commander    │
                              │  • Email→ commander    │
                              │  (Orange + Red only)   │
                              └────────────────────────┘
```

---

## AWS Resources

### 1. S3 Bucket — `swarmsight-demo-<yourname>`

| Property | Value |
|---|---|
| **Region** | `ap-south-1` |
| **Purpose** | Store demo MP4 clips; optionally store frame snapshots for post-event review |
| **Access** | Private. No public access. EC2 reads via instance profile only. |
| **Encryption** | SSE-S3 (AES-256) — enabled by default on all new buckets |
| **Lifecycle** | Optional: auto-expire `/snapshots/` prefix after 7 days |
| **Block Public Access** | All four settings enabled |

**Key prefixes:**
```
s3://swarmsight-demo-<yourname>/
  clips/
    normal-concert.mp4            <- "safe" demo clip
    compression-precursor.mp4    <- "danger escalation" demo clip
  snapshots/
    <zone_id>/<timestamp>.jpg     <- optional frame snapshots on alert
```

> **Privacy note:** Raw frames are never persisted by default. The
> `/snapshots/` prefix is opt-in, stores only the alerting frame grid
> (no individual faces), and auto-expires. This satisfies the
> responsible-AI constraint from the full solution doc.

---

### 2. EC2 Instance — `swarmsight-demo`

| Property | Value |
|---|---|
| **Instance type** | `t3.large` (2 vCPU, 8 GB RAM) — CPU-only inference at 2-5 fps |
| **AMI** | Ubuntu 22.04 LTS (Jammy), x86_64 |
| **Availability Zone** | `ap-south-1a` |
| **Storage** | 30 GB gp3 EBS root volume (model weights + code) |
| **IAM** | Instance profile `swarmsight-ec2-role` attached at launch |
| **Key pair** | Required for SSH — use an existing key pair or create one |

**Software stack (all installed on the instance):**
- Python 3.11 + pip
- FastAPI + Uvicorn (ASGI server)
- OpenCV (opencv-python-headless)
- PyTorch CPU build + CSRNet pretrained weights
- boto3 (AWS SDK — uses instance profile, no hardcoded credentials)

**Security Group: `swarmsight-sg`**

| Direction | Protocol | Port | Source | Purpose |
|---|---|---|---|---|
| Inbound | TCP | 22 | Your IP /32 | SSH management |
| Inbound | TCP | 8000 | Your IP /32 | FastAPI + WebSocket (demo) |
| Outbound | All | All | 0.0.0.0/0 | AWS API calls, package installs |

> **Why port 8000 only to your IP:** This is a demo, not a public service.
> Restricting to a single IP means the security group is the first line of
> defense even before any application-level auth.

**Process layout on the instance:**
```
systemd / screen session
└── uvicorn swarmsight.main:app --host 0.0.0.0 --port 8000 --workers 1
    ├── /ws/feed      (WebSocket — streams frames + density grid to browser)
    ├── /api/alerts   (REST — last N alerts from DynamoDB)
    └── /             (static — serves index.html, main.js)
```

---

### 3. DynamoDB Table — `swarmsight_alerts`

| Property | Value |
|---|---|
| **Region** | `ap-south-1` |
| **Billing mode** | On-demand (PAY_PER_REQUEST) — no capacity planning |
| **Partition key** | `zone_id` (String) — e.g. "grid_3_2", "zone_barrier_left" |
| **Sort key** | `timestamp` (Number) — Unix epoch in milliseconds |
| **Encryption** | AWS-managed key (default) |

**Item schema:**
```json
{
  "zone_id":          "grid_3_2",
  "timestamp":        1726773600000,
  "risk_level":       "RED",
  "density_value":    5.8,
  "baseline_density": 2.1,
  "motion_variance":  0.003,
  "flow_coherence":   0.87,
  "reason":           "Density 5.8/m2 (2.8x baseline) + micro-motion collapsed + wave coherence 0.87",
  "clip_name":        "compression-precursor.mp4",
  "ttl":              1727378400
}
```

**Access patterns:**

| Query | Operation | Key condition |
|---|---|---|
| Latest alerts for dashboard | Query | Any zone_id, timestamp descending |
| All alerts in last 5 min | Scan + filter | timestamp > now - 300000 |
| Alerts for one zone | Query | zone_id = "grid_3_2" |

> **TTL:** Enable on the `ttl` attribute (Unix epoch seconds). Items
> auto-expire after 7 days. Keeps the table clean between demo runs.

---

### 4. SNS Topic — `swarmsight-alerts`

| Property | Value |
|---|---|
| **Region** | `ap-south-1` |
| **Topic type** | Standard (not FIFO — ordering not required for alerts) |
| **Display name** | `SwarmSight Alert` |
| **Subscriptions** | SMS (commander phone) + Email (commander address) |

**Alert policy (enforced in application code):**
- Only `Orange` and `Red` risk-level transitions publish to SNS.
- `Green` → `Yellow` transitions are shown on dashboard only (no SMS/email).
- **Debounce:** A zone must hold the alert state for >= 3 consecutive frames
  before publishing. Prevents single-frame noise from triggering alerts.
- **State-transition only:** A zone already at `Red` does not re-publish
  unless it drops back to `Yellow`/`Green` and re-escalates.
- **Cooldown:** Minimum 60 seconds between SNS publishes for the same zone.

**SNS message payload:**
```json
{
  "default": "ALERT SwarmSight — Zone grid_3_2 escalated to RED. Density 5.8/m2 (2.8x baseline). Micro-motion collapsed. Involuntary wave detected. Immediate review required."
}
```

> WARNING: Confirm subscriptions before the demo. SNS sends a confirmation
> email/SMS when you subscribe. Unconfirmed subscriptions silently receive
> nothing. Do this in Step 0 of the build, not during the demo run.

---

### 5. IAM — Instance Profile `swarmsight-ec2-role`

See `iam-policy-design.md` for the full policy JSON and design rationale.

**Summary of permissions granted:**

| Service | Actions | Resource scope |
|---|---|---|
| S3 | GetObject, PutObject, ListBucket | Named bucket only |
| DynamoDB | PutItem, Query, Scan | Named table ARN only |
| SNS | Publish | Named topic ARN only |

No wildcard resources. No IAM, EC2, or admin actions. No credentials stored on disk.

---

## Data Flow — Frame Processing Pipeline

```
[S3 clip or local file]
        |
        | cv2.VideoCapture (frame-by-frame at ~5 fps)
        v
[Frame buffer (latest 2 frames)]
        |
   ┌────┴────────────────────────────────────┐
   |                                         |
   v                                         v
[CSRNet density model]              [Farneback optical flow]
[→ density map 8x6 grid]            [→ per-cell flow vectors]
        |                                    |
        └────────────┬───────────────────────┘
                     v
         [Risk Fusion Engine]
         ┌─────────────────────────────────────────┐
         | Per cell, per frame:                    |
         |  1. density vs. per-zone rolling baseline|
         |  2. micro-motion variance vs. baseline  |
         |  3. flow coherence across neighbors     |
         |  4. combined → Green/Yellow/Orange/Red  |
         └─────────────────┬───────────────────────┘
                           |
              ┌────────────┼──────────────────┐
              |            |                  |
              v            v                  v
     [WebSocket push]  [DynamoDB]          [SNS]
     (frame + grid    PutItem on          Publish on
      to browser)     Orange/Red          Orange/Red
                      transition          transition
```

---

## Mapping to Full Production Architecture

| Demo (10 hr) | Production equivalent |
|---|---|
| MP4 clips on S3 / EC2 local | Kinesis Video Streams (live drone RTSP) |
| FastAPI WebSocket on EC2 | API Gateway WebSocket + CloudFront |
| CSRNet CPU inference on EC2 | SageMaker real-time endpoint (GPU) |
| Farneback OpenCV CPU | SageMaker RAFT optical flow endpoint |
| Python rule engine | XGBoost risk classifier (SageMaker) |
| DynamoDB (alerts only) | DynamoDB (live state) + Timestream (time-series) |
| Manual demo run | Step Functions (orchestrated pipeline) |
| Security group IP lock | Cognito RBAC + WAF |
| Single region | Multi-AZ with Auto Scaling |

---

## Cost Estimate (demo day only)

| Resource | Est. cost |
|---|---|
| EC2 t3.large (10 hours) | $0.08/hr x 10 hr = ~$0.80 |
| S3 storage (< 1 GB) | < $0.03 |
| DynamoDB on-demand (< 1000 writes) | < $0.01 |
| SNS (< 10 publishes) | < $0.01 |
| Data transfer | < $0.10 |
| **Total** | ~$1.00 |

> Terminate the EC2 instance immediately after the demo to stop billing.
> S3 and DynamoDB incur negligible ongoing costs even if left running.
