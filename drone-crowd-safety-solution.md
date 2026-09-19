# SwarmSight — Predictive Crowd Safety Intelligence from Aerial Video

A responsible, AI-heavy, AWS-native platform that turns live drone feeds into
**anticipatory** situational awareness for incident commanders — not just
"count the crowd," but forecast danger before it becomes a stampede.

---

## 1. The Core Idea That Differentiates This Submission

Most teams tackling this brief will build: **YOLO/person-detector → count →
heatmap → dashboard.** That's a commodity solution and will be the median
entry among 10,000 applicants. It also breaks down exactly where it matters
most — dense crowds, where individual detection fails due to occlusion.

SwarmSight is built around three ideas judges will not see often:

1. **Predictive, physics-grounded risk scoring, not just detection.**
   Real crowd disasters (Love Parade 2010, Hillsborough 1989, Itaewon 2022)
   share a signature: density rises *while forward velocity collapses toward
   zero* — a "stop-and-crush" pattern, not just high density alone. We detect
   this precursor pattern (grounded in Fruin's Level-of-Service scale and
   G. Keith Still's crowd density/risk framework) minutes before it becomes
   critical, instead of alerting only once density is already dangerous.
2. **Density-map regression, not object detection, for the actual crowd
   science.** Aerial crowds at events regularly exceed 4–6 people/m². Box
   detectors collapse under occlusion at this density. We use density-map
   regression (CSRNet/Bayesian-loss-style architectures), fine-tuned on
   drone-specific datasets (VisDrone, DroneCrowd), which is the technically
   correct approach and something most entrants won't know to do.
3. **Responsible-AI-by-construction, matched to the brief's own wording.**
   The prompt explicitly says "responsible" and "authorised control rooms."
   Most entries will treat that as a throwaway phrase. We treat it as a
   design constraint: no facial recognition, no individual tracking/ID, raw
   frames auto-discarded after feature extraction, human-in-the-loop alerting
   (the system recommends, commanders decide), full audit trail, and
   role-gated access. This is what separates a hackathon toy from something
   a real police/event-safety agency could actually deploy — and it's the
   kind of thing a judging panel with public-safety stakeholders rewards.

---

## 2. System Architecture (AWS-native, end to end)

```
DRONE(S) / GROUND STATION
   │  RTSP/RTMP video + GPS/IMU telemetry
   ▼
[Edge: AWS Panorama or IoT Greengrass on ground-station compute]
   - lightweight on-device inference (fallback if uplink drops)
   - frame sampling, compression, telemetry tagging
   ▼
Amazon Kinesis Video Streams  ──────────────► S3 (raw clips, lifecycle-expired)
   │
   ▼
Step Functions (pipeline orchestration)
   │
   ├─► SageMaker Endpoint #1: Crowd Density Estimation (density-map CNN)
   ├─► SageMaker Endpoint #2: Flow/Motion Estimation (RAFT optical flow + ByteTrack for sparse zones)
   ├─► SageMaker Endpoint #3: Scene Segmentation (paths, barriers, stages, vehicles, choke points)
   │
   ▼
Risk Fusion Engine (Lambda/ECS Fargate)
   - combines density, density-gradient-over-time, flow convergence,
     velocity collapse, historical zone baseline
   - XGBoost/gradient-boosted risk classifier, trained partly on
     SYNTHETIC crowd-crush data generated via social-force-model
     simulation (real catastrophic-event data is scarce — simulate it)
   ▼
Amazon Timestream (time-series per-zone metrics) + DynamoDB (live zone/alert state)
   ▼
Amazon Location Service + custom graph engine (Lambda)
   - dynamic evacuation/access corridor graph
   - edge weights = live density; recomputes safe routes as congestion shifts
   - multi-route redundancy (never a single-path recommendation)
   ▼
EventBridge → SNS/SES (severity alerts to authorised commanders: push, SMS, email)
   ▼
API Gateway (WebSocket) ↔ Live Control Room Dashboard (Amplify + CloudFront + S3)
   - geo-referenced live heatmap, alert feed with severity tiers,
     "why this alert fired" explainability panel, recommended corridors

Cross-cutting: Cognito (RBAC — only authorised control-room roles),
KMS (encryption at rest), VPC isolation, CloudTrail (full audit log),
CloudWatch (ops monitoring), Auto Scaling on SageMaker endpoints.
```

### Why each AWS choice matters (say this explicitly to judges)
- **Kinesis Video Streams**: purpose-built for live drone/video ingest with
  durable storage and replay — not a generic Lambda-triggered polling hack.
- **Greengrass/Panorama at the edge**: the single biggest reliability gap in
  every competitor's design will be "what happens when the drone's uplink
  drops mid-event?" We answer it: local inference continues, sync on
  reconnect.
- **Timestream**: purpose-fit for the density/velocity time series that
  drives the predictive risk model — cheaper and faster than jamming this
  into DynamoDB or RDS.
- **Step Functions**: makes the multi-model pipeline auditable and
  demonstrably fault-tolerant (retries, per-stage failure isolation) —
  important for a public-safety system.
- **Location Service**: avoids reinventing mapping/geo primitives, while the
  custom graph engine on top is where the actual innovation (dynamic,
  density-aware rerouting) lives.

---

## 3. AI Model Stack in Detail

| Task | Approach | Why not the obvious choice |
|---|---|---|
| Crowd counting/density | Density-map regression CNN (CSRNet-style / Bayesian loss), fine-tuned on VisDrone + DroneCrowd (aerial-angle data) | Box detectors (YOLO) fail under heavy occlusion at high density — the exact regime that matters for safety |
| Movement/flow | RAFT optical flow for dense crowds; ByteTrack for sparse/medium zones | Single detector-based tracking breaks down exactly when density is dangerous |
| Scene/path segmentation | Fine-tuned segmentation model (Mask2Former/DeepLab) for pathways, barriers, stages, vehicles | Needed to know which routes are physically usable, not just geometrically shortest |
| Risk classification | Gradient-boosted classifier (density + Δdensity/Δt + flow convergence + velocity collapse) trained partly on **social-force-model simulated** stampede scenarios (e.g. PedSim-style simulation) | Real crowd-crush incident data barely exists publicly — simulate the rare, catastrophic class instead of pretending you have enough real examples |
| Forecasting | Short-horizon time-series extrapolation (per-zone) on Timestream data to project time-to-critical-density | Turns the system from reactive to anticipatory — the single biggest differentiator |

---

## 3.1 Handling Stationary Dense Crowds (e.g. Concerts) — Refined Risk Signal

**Problem this addresses:** during a performance, the audience is dense and
stationary by default. A naive "high density + low velocity = danger" rule
(the initial precursor framing above) would false-alarm constantly, since
that's the *normal* safe state of a concert crowd, not an anomaly. The risk
model needs a sharper signal to separate "happy packed crowd watching a
show" from "dangerously compressed crowd."

- **Micro-motion, not gross velocity.** Track local displacement *variance*
  per cell over short windows (2–5s). A healthy dense crowd retains
  small-scale sway/give; a dangerously compressed crowd loses micro-motion
  even at similar or higher density (people physically can't move). Falling
  micro-motion combined with sustained/rising density is the real signal —
  not stillness alone.
- **Involuntary wave coherence vs voluntary movement.** Dangerous crowds show
  correlated pressure waves propagating through the mass (people being
  pushed). Cheering/dancing is comparatively incoherent across the crowd, or
  synchronized to the music (cross-checkable against audio tempo). Measure
  optical-flow **coherence** between neighboring cells, not raw motion
  magnitude.
- **Baseline-relative anomaly detection.** Calibrate a per-zone baseline
  from the event's own early low-risk period; alert on deviation from that
  baseline rather than one fixed universal threshold. Density building
  rapidly at a barrier in 90 seconds is abnormal even if the same density
  sustained over an hour on the general floor is normal for that show.
- **Event-phase awareness.** Feed event phase (entry / performance / encore
  or set-transition / exit) into the risk model. Surges cluster at specific
  moments (artist walk-on, encore, transitions) — thresholds should adapt to
  phase rather than treat the whole show as one static regime.
- **Localize to interfaces, not the whole floor.** Real crush risk
  concentrates at barriers, stage-front barricades, and choke points. Track
  density *gradient* at those specific zones separately from bulk
  general-admission density.

Pitch-ready framing: *"We don't alert on stillness — a concert crowd is
stationary and safe. We alert on loss of micro-motion combined with
sustained density, and on involuntary pressure waves distinct from voluntary
movement like dancing, calibrated against each event's own baseline and
phase."*

---

## 4. Responsible-AI / Governance Layer (make this a headline feature, not a footnote)

- **No facial recognition, no individual re-identification.** Only
  aggregate density/flow features leave the pipeline; raw frames are
  discarded after feature extraction per a defined retention window.
- **Human-in-the-loop by design.** The system never autonomously triggers
  physical actions (barriers, drone routing changes); it surfaces
  recommendations with confidence and rationale to a human commander.
- **Explainability panel.** Every alert shows the underlying density trend,
  flow vectors, and which precursor pattern fired — commanders won't trust
  or act on a black box.
- **Access control & audit.** Cognito RBAC restricts the dashboard to
  authorised control-room roles; CloudTrail logs every access and action for
  post-incident review.
- **Regulatory awareness.** Explicitly scope the pitch around drone
  operation authorisation (civil aviation rules) and data-protection
  compliance for public-space monitoring — mentioning this unprompted signals
  maturity judges rarely see.

---

## 5. Build Roadmap

**Phase 0 — Foundations (Week 1)**
- Lock architecture, AWS account/IAM setup, collect datasets (VisDrone,
  DroneCrowd), set up a social-force-model simulator for synthetic
  crowd-crush training data.

**Phase 1 — Core density model (Weeks 2–3)**
- Fine-tune density-map regression model on aerial data, validate against
  held-out drone footage, deploy to a SageMaker real-time endpoint.

**Phase 2 — Flow + risk fusion (Weeks 3–4)**
- Optical flow/tracking pipeline, build the risk-fusion classifier,
  calibrate severity thresholds against Fruin LOS / Still risk bands,
  generate and incorporate synthetic stampede-precursor training data.

**Phase 3 — Corridor mapping & routing (Weeks 4–5)**
- Segmentation model for paths/obstacles; dynamic graph engine for
  evacuation/access routing with live density-weighted edges and
  multi-route redundancy.

**Phase 4 — Ingestion & edge resilience (Weeks 5–6)**
- Kinesis Video Streams integration; Greengrass/Panorama edge inference for
  offline continuity; Step Functions orchestration of the full pipeline.

**Phase 5 — Dashboard & alerting (Weeks 6–7)**
- Live map dashboard (heatmap + corridors + alert feed + explainability
  panel), WebSocket push, SNS/SES alerting, Cognito RBAC.

**Phase 6 — Integration test & demo prep (Weeks 7–8)**
- End-to-end test with real or recorded drone footage of a crowded scene,
  load/failure testing (simulate a dropped uplink mid-demo and show
  edge-mode continuity — this is a strong live-demo moment), cost analysis,
  architecture diagram, and a tight demo script.

**Phase 7 — Polish**
- Demo video, one-pager, judge Q&A prep (be ready to defend: why density
  maps over detectors, why synthetic training data, how privacy is
  enforced, cost per event-hour at scale).

---

## 6. Competitive Strategy — How to Actually Beat 10,000 Entries

1. **Lead with the precursor-detection insight, not the tech stack.** Open
   your pitch with the stop-and-crush pattern and cite a real incident
   timeline (e.g., how long it took human stewards to notice at Love Parade
   vs. how fast a density/velocity trend model would have flagged it). This
   reframes the whole pitch around lives saved, not feature count.
2. **Show a working live demo, even scaled down.** A real pipeline running
   on recorded aerial footage, with a live dashboard and a triggered alert,
   beats a slide deck every time. Most teams will not have a working
   end-to-end system by deadline — being one of the few that does is itself
   a huge differentiator.
3. **Demonstrate genuine AWS depth, not buzzword-dropping.** Be ready to
   explain *why* Timestream over DynamoDB for time series, *why*
   Greengrass for edge resilience — judges evaluating "shipped on AWS" can
   tell surface-level usage from real architectural reasoning.
4. **Make "responsible" a scored feature, not a disclaimer.** Since the
   brief itself uses that word, explicitly walk through your privacy and
   human-in-the-loop design in the pitch. Most entrants will skip this
   entirely, treating it as compliance boilerplate.
5. **Quantify impact.** Even rough numbers (e.g., "reduces time-to-alert
   from ~X minutes of manual observation to under Y seconds") make the pitch
   concrete and memorable to a panel that will see hundreds of
   feature-listing pitches.
6. **Have a rehearsed fallback for the demo.** If a model isn't fully
   trained by presentation day, replay a pre-recorded scenario through the
   live architecture and say so honestly — a transparent, working partial
   system outbeats a fragile "it worked yesterday" full system.
