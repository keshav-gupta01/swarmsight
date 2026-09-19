# SwarmSight — 10-Hour Build Plan (Scoped-Down Demo Architecture)

This is a deliberately smaller architecture than the full production design in
the solution doc — the goal here is a working, live, judge-demoable pipeline
in 10 hours, not a production system. Cut vs. the original: no Kinesis Video
Streams, no SageMaker-managed endpoints, no Greengrass edge, no Step
Functions, no Timestream, no Amazon Location Service routing. Everything
runs on **one EC2 instance** with **pretrained weights only (no training)**
and **rule-based risk fusion (no ML training)**. State this scoping decision
explicitly in your pitch — it reads as engineering judgment, not as a gap.

**Demo scenario:** two pre-recorded aerial crowd clips streamed frame-by-frame
to simulate a live drone feed — one normal packed concert crowd (should stay
green/safe), one showing a compression/crush precursor (should escalate and
alert). This directly demonstrates the stationary-crowd handling discussed
earlier.

---

## Architecture decisions (locked — no debate needed mid-build)

| Layer | Choice | Why (given 10 hrs) |
|---|---|---|
| Compute | 1x EC2 instance, FastAPI app (serves API + WebSocket + static frontend) | No SageMaker packaging/deploy overhead; one thing to debug |
| Video "ingestion" | Local/S3-hosted MP4 clips, read frame-by-frame and pushed over WebSocket | Simulates live drone feed without needing real hardware or Kinesis setup |
| Density model | Pretrained crowd-counting density-map model (CSRNet/MCNN/Bay-loss — inference only, no training) | Keeps the "density-map regression, not detection" differentiator without a training budget |
| Motion/coherence | OpenCV Farneback dense optical flow, grid-cell variance + coherence | Zero training needed, runs in real time on CPU |
| Risk fusion | Python rule engine (density thresholds + coherence drop + baseline deviation) | No time to generate synthetic training data and train a classifier — rules are honest and explainable, which is itself the pitch |
| Storage | DynamoDB (alerts), S3 (demo clips) | Minutes to set up, no schema migration pain |
| Alerting | SNS (SMS/email) | Real, live-firing alert is a strong demo moment, ~10 min to wire |
| Frontend | Single HTML/JS page, canvas overlay on video, WebSocket live updates | No build tooling, no framework setup time |
| Security | IAM role scoped to the 3 resources above, security group locked to your IP | Minimal but real — matters for the "responsible" narrative |

---

## Step 0 — AWS Foundation (0:00–0:30)

Working checkpoint: **AWS resources exist and are reachable.**

**Two ways to do this — pick one, time-boxed:**

- **Option A (default): you configure manually** via console/CLI, per the
  list below. Reliable, no new tooling, ~30 min.
- **Option B: let Antigravity provision it via the official AWS MCP Server**
  (`uvx mcp-proxy-for-aws` pointed at `https://aws-mcp.us-east-1.api.aws/mcp`,
  added to Antigravity's `mcp_config.json` under **MCP Servers → Manage MCP
  Servers → View raw config**). Requires `uv` installed and AWS CLI
  credentials already configured locally. **Hard time-box: 15 minutes.** If
  it isn't connected and creating resources by then, abandon it and fall
  back to Option A immediately — don't let a first-time MCP hiccup eat your
  only slack-free window. If it works cleanly, it's also a good pitch line
  (agent-provisioned, IAM-scoped, CloudTrail-audited infra).

Either way, you need exactly these resources:

1. **S3 bucket**: `swarmsight-demo-<yourname>` — upload your two demo video
   clips here (or just keep them local on the EC2 box if upload is slow;
   S3 is nice-to-have, not blocking).
2. **IAM role** `swarmsight-ec2-role`: attach policies scoped to that one S3
   bucket, one DynamoDB table (create in step below), and one SNS topic.
   Attach as an **instance profile** to the EC2 instance so the app never
   needs hardcoded credentials.
3. **EC2 instance**: `t3.large` (CPU is fine — target 2–5 fps for the demo,
   which is plenty to look live). If you have GPU budget/quota, `g4dn.xlarge`
   makes the density model smoother, but don't burn time fighting GPU driver
   setup under a 10-hour clock — CPU is the safe default.
   - Security group: open port `8000` to **your IP only**, port `22` (SSH)
     to your IP only.
4. **DynamoDB table** `swarmsight_alerts`: partition key `zone_id` (String),
   sort key `timestamp` (Number). On-demand capacity mode (no planning
   needed).
5. **SNS topic** `swarmsight-alerts`: subscribe your phone (SMS) and/or
   email, **confirm the subscription now** (SNS emails a confirm link —
   don't discover this mid-demo).

Verification: SSH into the EC2 box, `aws s3 ls`, `aws dynamodb list-tables`,
`aws sns list-topics` all succeed without hardcoded keys (proves the IAM
role is attached correctly).

---

## Step 1 — Backend skeleton + simulated live feed (0:30–2:00)

Working checkpoint: **Opening the EC2 public IP:8000 in a browser shows the
demo video playing, streamed live from the backend.**

- Antigravity task: FastAPI app with a WebSocket endpoint that reads a video
  file frame-by-frame (OpenCV `VideoCapture`), encodes each frame as
  JPEG/base64, and pushes it over the socket at a fixed rate (throttle to
  ~5–8 fps intentionally — don't just blast frames as fast as possible).
- Minimal `index.html` with a `<canvas>` that receives frames over
  WebSocket and draws them.
- This step validates the entire transport path (EC2 → browser) before any
  AI is involved — if this doesn't work, nothing downstream will, so get it
  solid first.

---

## Step 2 — Crowd density model (2:00–4:00)

Working checkpoint: **A live heatmap overlay is drawn on top of the video,
updating as the clip plays.**

- Antigravity task: find and integrate a **pretrained** crowd-counting
  density-map model (CSRNet, MCNN, or Bayesian-loss — search for an
  open-source repo with downloadable checkpoint weights, e.g. trained on
  ShanghaiTech/UCF-QNRF). Inference only — do not attempt to train or
  fine-tune anything in this window.
- Run inference per frame (or every 2nd–3rd frame if too slow), output a
  density map, downsample it to a coarse grid (e.g. 8×6 cells), send grid
  values over the same WebSocket alongside the frame.
- Frontend: draw a semi-transparent color-graded overlay per grid cell
  (green→yellow→orange→red by density) on the canvas.
- **Time-box this to 2 hours hard.** If no pretrained density model
  integrates cleanly by 3:30, fall back immediately to YOLOv8 person
  detection + per-cell head count as a density proxy — it's a lower-fidelity
  approximation, but a working fallback beats a broken "correct" approach.
  State the fallback honestly in the pitch if you use it.

---

## Step 3 — Motion coherence + risk fusion (4:00–5:30)

Working checkpoint: **Each grid cell shows a live severity color
(green/yellow/orange/red) that responds to both density and motion, not
density alone.**

- Antigravity task: compute Farneback dense optical flow between consecutive
  frames; per grid cell, compute (a) mean flow magnitude, (b) local
  displacement **variance** over a short rolling window (this is the
  micro-motion signal from the concert discussion).
- Risk rule (encode exactly this logic):
  - Maintain a **per-zone rolling baseline** of density and micro-motion
    variance from the first ~15–20 seconds of each clip (calibration
    window).
  - Flag risk when: density is high **and** micro-motion variance drops
    significantly **below that zone's own baseline** (compression signal) —
    not on high density or low velocity alone.
  - Separately flag risk when local flow shows high **coherence** in one
    direction across neighboring cells at high density (involuntary wave
    signal), distinct from incoherent/omnidirectional motion (dancing,
    cheering).
- Map the combined score to Green/Yellow/Orange/Red per Fruin-style bands,
  send per-cell severity over WebSocket.

---

## Step 4 — Alerting (5:30–6:30)

Working checkpoint: **When a zone crosses Orange/Red, a row appears in
DynamoDB and a real SMS/email arrives.**

- Antigravity task: on severity transition to Orange/Red, write an item to
  `swarmsight_alerts` (zone_id, timestamp, density, motion_coherence,
  risk_level, human-readable reason string) and publish to the SNS topic.
- Debounce so a zone doesn't spam an alert every frame — only on
  state transition (e.g. Yellow→Orange), plus a cooldown window.

---

## Step 5 — Dashboard polish (6:30–8:00)

Working checkpoint: **A control-room-style single page: video+heatmap on one
side, live alert feed with explainability on the other.**

- Alert feed panel: severity-colored list, each entry expandable to show
  *why* it fired (density value, baseline deviation, coherence value) — this
  explainability panel is a differentiator, don't skip it for cosmetics.
- Simple legend for severity bands.
- If time remains: a static venue-plan image with predefined corridor lines
  that recolor based on which zone they pass through (this is a
  **stretch goal**, not required for a working demo — cut first if behind
  schedule).

---

## Step 6 — Stationary-crowd validation (8:00–9:00)

Working checkpoint: **You can play the "normal concert crowd" clip and show
it staying green throughout, then play the "compression" clip and show it
escalating to red with an alert — live, side by side or back to back.**

- This step exists specifically to prove the fix discussed earlier actually
  works, and it's your strongest demo moment — rehearse narrating it: *"This
  is a packed, stationary crowd enjoying a show — no alert. This is the same
  density, but micro-motion collapses and a pressure wave propagates —
  alert fires in real time."*
- If baseline calibration is fighting you, a simpler fallback: hardcode the
  two clips' known-good baseline windows rather than fully generalizing the
  calibration logic. It only needs to work for your two demo clips today.

---

## Step 7 — Buffer, integration test, pitch rehearsal (9:00–10:00)

- Full run-through, at least twice.
- Have the EC2 instance already running and the browser tab already open
  before judges arrive — don't cold-start anything live.
- Prepare the one-line scoping statement: *"For the 10-hour build we run
  everything on a single EC2 instance with pretrained models and rule-based
  fusion to prove the concept live; the production architecture (Kinesis
  Video Streams, SageMaker endpoints, edge inference via Greengrass,
  Timestream, dynamic corridor routing) is scoped in our submission doc."*

---

## Antigravity skills to install for this build

Install the **official AWS agent skill bundle** — it's maintained by AWS,
covers exactly the services you're using (S3, EC2, DynamoDB, IAM, SNS, SDK
usage), and works across agents including Antigravity:

```
npx skills add aws/agent-toolkit-for-aws
```

This pulls in the `aws-core` skill set (service selection, SDK/boto3 usage,
storage, IAM, observability) — enable that one. **Skip the CDK/serverless
plugin** for this build: infrastructure-as-code (CDK bootstrap/synth/deploy)
adds setup and debugging time you don't have in a 10-hour window; you're
configuring AWS manually via console/CLI per Step 0, so Antigravity only
needs correct boto3/SDK usage guidance, not IaC tooling.

If you want one more, `aws-cdk-development` (zxkane) is confirmed to work in
Antigravity via the skills CLI — but only add it if you finish early and
want to convert the manual setup into reproducible IaC as a stretch/bonus
for the judges, not before.

Do **not** install broad general-purpose "full-stack" or "300+ skill vault"
bundles for this build — more skills loaded means more semantic-matching
overhead and more chances Antigravity reaches for an unfamiliar pattern
under time pressure. Keep the skill surface small and matched exactly to
what you're building.
