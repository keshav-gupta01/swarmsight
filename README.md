# 🚁 SwarmSight — Predictive Aerial Drone Crowd Safety Intelligence

[![AWS](https://img.shields.io/badge/AWS-ap--south--1-orange?logo=amazon-aws)](https://aws.amazon.com)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green?logo=fastapi)](https://fastapi.tiangolo.com)
[![OpenCV](https://img.shields.io/badge/OpenCV-Computer%20Vision-blue?logo=opencv)](https://opencv.org)
[![License](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

> **Predicting crowd crush precursors 5–15 minutes before catastrophic surges occur using aerial drone optical flow and micro-motion variance collapse.**

---

## 🌐 Live 24/7 Platform Demonstration

The SwarmSight platform is actively deployed and running 24/7 on AWS:

👉 **[Launch SwarmSight Mission Control Console](http://13.235.100.182:8000)**

* **Direct URL**: `http://13.235.100.182:8000`
* **Region**: `ap-south-1` (Mumbai)
* **Instance**: `i-0beef7562efedbba0` (`t3.large`, persistent `systemd` daemon)
* **Status**: Live & Publicly Accessible

---

## 📌 The Problem: Why Traditional Crowd Analytics Fail

From the Hillsborough stadium disaster (1989) and Love Parade (2010) to the Itaewon tragedy (2022), crowd crush disasters follow an identical pattern: **density alone does NOT kill; sudden loss of personal physical space and micro-motion collapse does.**

Traditional computer vision approaches fail in high-density crowd scenarios:
1. **Object Detectors (YOLO/Faster-RCNN) Break Down**: In crowds with 4–8 people/m², bounding box detectors fail due to severe occlusion.
2. **The "Packed Concert" False Alarm**: At music festivals or sporting events, crowds are naturally dense and stationary while enjoying a show. Naive density counters trigger continuous false alarms.
3. **Reactive, Not Anticipatory**: Traditional surveillance cameras alert security only after crowds have already surged or collapsed into a fatal stampede.

---

## 💡 The Innovation: Micro-Motion Variance & Flow Coherence

SwarmSight models crowd dynamics as physical fluid compression. Using high-angle aerial drone video feeds, SwarmSight partitions monitored sectors into an **8×6 spatial grid (48 zones)** and computes two foundational physics signals:

### 1. Micro-Motion Variance Collapse ($\sigma^2$)
In a safe, healthy packed crowd (such as a festival audience), individuals naturally sway, shift their weight, and move their arms. This generates persistent **high micro-motion jitter ($\sigma^2 > 0.40$)**. 

When a dangerous bottleneck or crowd crush develops, personal physical space collapses. Individuals become physically locked together by surrounding compressive forces, causing micro-motion variance to drop towards zero ($\sigma^2 < 0.25$). **This variance collapse provides a 5–15 minute predictive window before dangerous physical crushing forces manifest.**

$$\sigma^2 = \frac{1}{N} \sum_{t=1}^N (v_t - \mu_v)^2$$

### 2. Directional Flow Coherence ($C$)
Coherence measures whether crowd movement is chaotic and random ($C \approx 0$) or uniform and directional ($C \approx 1.0$). High coherence coupled with high velocity indicates an involuntary surge or shockwave propagating through the crowd.

$$C = \frac{\left\| \sum_{i=1}^M \vec{v}_i \right\|}{\sum_{i=1}^M \left\| \vec{v}_i \right\|}$$

---

## 🏗️ Architecture & AWS Cloud Design

SwarmSight is built with AWS CDK under strict least-privilege IAM security.

```
                              AERIAL DRONE FEEDS
                         (Safe Crowd vs. Compression)
                                      │
                                      ▼
                   ┌──────────────────────────────────────┐
                   │    AWS EC2 (Ubuntu 22.04 LTS)        │
                   │        Instance: t3.large            │
                   │  ┌────────────────────────────────┐  │
                   │  │  FastAPI + Uvicorn Application │  │
                   │  │  - Farneback Optical Flow Engine│  │
                   │  │  - 8x6 Grid Risk Scoring       │  │
                   │  │  - Debounced Alert Manager     │  │
                   │  └───────────────┬────────────────┘  │
                   └──────────────────┼───────────────────┘
                                      │
             ┌────────────────────────┼────────────────────────┐
             ▼                        ▼                        ▼
┌─────────────────────────┐ ┌───────────────────┐ ┌─────────────────────────┐
│     AWS S3 Bucket       │ │   AWS DynamoDB    │ │       AWS SNS Topic     │
│ (swarmsight-demo-kesha) │ │(swarmsight_alerts)│ │   (swarmsight-alerts)   │
│ - Private storage       │ │- Audit log records│ │- Real-time Email alerts │
│ - Video clips & assets  │ │- 7-Day Auto TTL   │ │- 1-hour anti-spam cooldown│
└─────────────────────────┘ └───────────────────┘ └─────────────────────────┘
             ▲                        ▲                        ▲
             │                        │                        │
             └─────────────── IAM Instance Profile ────────────┘
                         (Zero Hardcoded Credentials)
```

### AWS Services Utilized:
* **Amazon EC2 (`t3.large`)**: Runs the real-time computer vision engine, WebSocket broadcast daemon, and API server. Managed via a persistent `systemd` daemon with automatic restart on boot.
* **Amazon S3 (`swarmsight-demo-kesha`)**: Private storage for demo video clips and frame snapshots with AES-256 server-side encryption.
* **Amazon DynamoDB (`swarmsight_alerts`)**: Stores timestamped incident records and telemetry breakdowns with an automated 7-day Time-To-Live (TTL).
* **Amazon SNS (`swarmsight-alerts`)**: Real-time dispatch of ORANGE and RED emergency alerts to safety commanders with incident aggregation and a 1-hour email cooldown.
* **AWS IAM**: Strict least-privilege instance profile granting EC2 access to only the project's named S3, DynamoDB, and SNS resources without credentials on disk.

---

## 🖥️ Mission Control Web Console

The browser-based dashboard provides incident commanders with instantaneous situational awareness:

* **Real-Time Video Canvas**: Streams base aerial frames with dynamic, semi-transparent risk heatmaps rendered at ~7 FPS over sub-150ms WebSockets.
* **Drone Feed Toggle**:
  * **Drone Alpha (Safe Crowd)**: Simulates a packed stadium concert with high density but healthy sway variance (remains **GREEN**).
  * **Drone Bravo (Compression Precursor)**: Simulates a bottleneck choke point where variance collapses, triggering anticipatory **ORANGE / RED** alerts.
* **Interactive Zone Inspector HUD**: Hover over any sector in the 8×6 grid to inspect real-time metrics: density percentage, optical flow velocity, variance, and coherence.
* **Explainability Audit Feed**: Real-time feed detailing the exact metric thresholds that triggered each alert, cross-referenced with DynamoDB persistence and SNS dispatches.
* **Web Audio Synthesizer**: Auditory alert chimes generated via the browser Web Audio API on critical threshold breaches.

---

## 📂 Repository Structure

```
├── app/
│   ├── __init__.py
│   ├── alerting.py          # Stateful debouncing, incident aggregation & DynamoDB/SNS dispatch
│   ├── analytics.py         # Farneback optical flow & micro-motion variance engine (8x6 grid)
│   ├── generate_clips.py    # Simulation clip generator for safe crowd vs. compression precursor
│   ├── main.py              # FastAPI server, WebSocket endpoint (/ws/stream), REST APIs
│   └── static/
│       └── index.html       # HTML5 Canvas dark-mode Mission Control console
├── docs/
│   ├── architecture.md      # Detailed AWS cloud architecture specification
│   ├── deployment-plan.md   # Deployment blueprints and step-by-step checklist
│   ├── iam-policy-design.md # Least-privilege IAM policy definitions
│   └── mvp-implementation-summary.md # Post-deployment verification report
├── infra/
│   ├── bin/
│   │   └── swarmsight.ts    # CDK app entry point
│   ├── lib/
│   │   ├── config.ts        # Infrastructure configuration constants
│   │   └── swarmsight-stack.ts # Complete AWS CDK stack definition (TypeScript)
│   ├── cdk.json
│   ├── package.json
│   └── tsconfig.json
├── drone-crowd-safety-solution.md # Comprehensive end-to-end enterprise solution proposal
└── README.md
```

---

## 🚀 Quickstart & Local Setup

### Prerequisites
* Python 3.10+
* Node.js 18+ and AWS CDK v2 (for infrastructure deployment)
* AWS CLI configured (if running AWS services locally)

### 1. Clone & Set Up Python Environment
```bash
git clone https://github.com/keshav-gupta01/swarmsight.git
cd swarmsight

# Create and activate virtual environment
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install fastapi "uvicorn[standard]" opencv-python-headless numpy boto3
```

### 2. Generate Demo Simulation Clips
```bash
python app/generate_clips.py
```

### 3. Run Mission Control Locally
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Open your browser and navigate to: `http://localhost:8000`

---

## 🛡️ Responsible AI Principles

SwarmSight is built with safety, privacy, and ethics as primary design constraints:
* **No Facial Recognition**: The platform analyzes spatial density and dense optical flow vectors. No biometric identification, face matching, or individual tracking is performed.
* **Zero Frame Archival**: Video frames are analyzed in-memory and discarded. Only aggregate numerical telemetry and sector risk scores are persisted to DynamoDB.
* **Human-in-the-Loop Operations**: SwarmSight acts as an advisory early-warning system. Alerts notify authorized human commanders who verify context before deploying physical crowd interventions.

---

## 🗺️ Production Upgrade Roadmap

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ Phase 1: Ingest   │ AWS Kinesis Video Streams (KVS) for multi-drone WebRTC/HLS ingest  │
├───────────────────┼────────────────────────────────────────────────────────────────────┤
│ Phase 2: Edge     │ AWS IoT Greengrass / Panorama on ground stations for local fallback│
├───────────────────┼────────────────────────────────────────────────────────────────────┤
│ Phase 3: AI       │ CSRNet/Bayesian density maps + GPU-accelerated RAFT optical flow   │
├───────────────────┼────────────────────────────────────────────────────────────────────┤
│ Phase 4: Time     │ Amazon Timestream for per-second metric trend forecasting          │
├───────────────────┼────────────────────────────────────────────────────────────────────┤
│ Phase 5: Routing  │ Amazon Location Service + NetworkX for dynamic safe egress routing │
├───────────────────┼────────────────────────────────────────────────────────────────────┤
│ Phase 6: Workflow │ AWS Step Functions for multi-agency dispatch & approval pipelines  │
└───────────────────┴────────────────────────────────────────────────────────────────────┘
```

---

## 📜 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
