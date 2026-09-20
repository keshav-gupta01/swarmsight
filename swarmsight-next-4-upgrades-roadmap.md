# SwarmSight — Next 4 Upgrades: Combined Roadmap for Antigravity

Scope: containerize, replace the demo loop with a real live feed, add
predictive forecasting, and integrate the aerial fine-tuned model. Ordered
by dependency and priority, not by the order they were listed.

## Hard constraint, applies to every step below

**The public URL (domain + Elastic IP) must never change.** Every step here
is designed to be deployed onto the *same* EC2 instance behind the *same*
Elastic IP and the *same* Nginx/domain config. Concretely:
- Never touch the Route 53 record, never re-associate the Elastic IP, never
  change the security group's public inbound rule.
- Every redeploy is a **swap on the same instance**: bring the new
  container up on a spare local port, health-check it, then flip Nginx's
  upstream to point at the new port and reload (`nginx -s reload`), then
  stop the old container. This gives near-zero downtime and the link never
  moves.
- Verify the link after *every single step* below before moving to the
  next one. If a step ever requires touching DNS/IP/security group to work,
  stop and reconsider the approach rather than doing it.

---

## Step 1 — Containerize (do this first)

**Why first:** every subsequent change becomes "build a new image, health-
check it, swap it in" instead of hand-editing a live process. This is what
makes the later steps safe to do without risking the link.

- Write a `Dockerfile` for the FastAPI app (base image with Python +
  OpenCV + torch/whatever the density model needs).
- Install **Docker** on the EC2 instance if not already present.
- Replace the current systemd-managed bare `uvicorn` process with a
  systemd unit that runs `docker run` (or a small `docker-compose.yml`),
  still `Restart=always`.
- Nginx config stays pointed at the same local port the container exposes
  — nothing about the public-facing config changes here.
- **Working checkpoint:** `docker build` + `docker run` locally reproduces
  the exact same working demo. Then deploy that same image to the EC2
  instance and confirm the public link still serves the same working demo,
  now running inside a container.
- **Link check:** same domain, same behavior, zero visible change to a
  judge hitting the URL.

---

## Step 2 — Kick off aerial fine-tuning (start now, runs in background)

**Why here:** training takes real wall-clock time independent of your
engineering work, and doesn't touch the live demo instance at all — so
start it now and let it run in parallel with Steps 3 and 4, integrating the
result at the very end (Step 5).

- Spin up a **separate GPU spot instance** (not the demo instance) per the
  fine-tuning guide already produced.
- Generate/cache the Gaussian density-map ground truth from your
  VisDrone/DroneCrowd point annotations.
- Start the fine-tuning run (low LR, from your existing pretrained
  checkpoint, MSE loss, early stopping on validation MAE).
- **Working checkpoint:** training is running unattended on its own
  instance; you can check in on loss/MAE periodically without it blocking
  any other work.
- **Link check:** not applicable yet — this step touches a completely
  separate instance. Confirm the demo link is unaffected (it should be,
  since nothing here touches it).

---

## Step 3 — Predictive forecasting (Timestream + trend extrapolation)

**Why before the live feed:** this is the headline "anticipatory, not
reactive" differentiator and matters more than the live-feed upgrade for
how the judges evaluate the core idea. It's also lower engineering risk
than swapping the ingestion source, so it's the better use of prime,
lower-risk time.

- Create a **Timestream** table; on each processed frame, write per-zone
  density and motion-coherence values as a time-series record (instead of,
  or alongside, the current in-memory/DynamoDB snapshot).
- Add a short-horizon trend extrapolation function per zone: fit a simple
  linear (or exponential, if density is accelerating) trend over the last
  N seconds of that zone's Timestream data, project forward to estimate
  **time-to-critical-density**.
- Surface it on the dashboard: replace or augment the current-state color
  badge with a line like *"Zone 3 projected to cross critical density in
  ~90s at current rate."*
- Since Step 1 is done, ship this as a new image build + swap-in, not a
  live edit.
- **Working checkpoint:** dashboard shows a live forecast string per zone
  that updates as density trends change, not just a static current-state
  color.
- **Link check:** confirm the swapped container serves the new dashboard
  at the same URL with no interruption a judge would notice.

---

## Step 4 — Replace the demo loop with a real live feed (WebRTC/RTSP)

**Why last of the three feature additions:** biggest "wow" factor, but also
the riskiest and most fiddly integration (real network conditions, phone
camera setup, browser permissions) — do it after the more important and
lower-risk forecasting feature is safely shipped, so a slip here doesn't
cost you the more important capability.

- Add a WebRTC (or RTSP, if simpler given your stack) ingestion endpoint
  that accepts a live stream from a phone/laptop camera and feeds frames
  into the exact same pipeline that currently reads the demo video file —
  the density/motion/risk/forecast logic doesn't change, only the frame
  source does.
- Keep the file-loop path available as a fallback input mode (toggle
  between "demo clip" and "live feed") — if venue Wi-Fi fails during
  judging, you can fall back instantly without redeploying anything.
- Test over the actual network conditions you'll present under, not just
  localhost — WebRTC/RTSP behavior over real Wi-Fi is where this usually
  breaks.
- **Working checkpoint:** pointing a phone camera at a crowded room updates
  the live dashboard (heatmap, risk, forecast) in real time.
- **Link check:** confirm again after this swap-in; this is the step most
  likely to introduce a regression, so test thoroughly before trusting it.

---

## Step 5 — Integrate the fine-tuned aerial model

**Why last:** this depends on Step 2's training finishing, and it's a
clean, self-contained swap once it does — best done once everything else
is stable so you're not debugging a model swap and a live-feed integration
at the same time.

- Once validation MAE looks good on the GPU training instance, copy the
  new checkpoint into the app's model directory (or S3, if you've moved to
  a SageMaker endpoint by now).
- Rebuild the container image with the new weights baked in (or pointed at
  the new S3 artifact), health-check locally, then swap in on the demo
  instance exactly as in every prior step.
- Do the old-vs-new visual comparison on a held-out aerial test clip as
  planned — good evidence for the pitch.
- **Working checkpoint:** live dashboard now runs the aerial fine-tuned
  model; heatmap quality visibly improves on real drone-angle footage
  compared to the original pretrained weights.
- **Link check:** final full run-through — live feed, forecasting, and the
  fine-tuned model all working together, same URL as day one.

---

## After all 4 are done

Run the full demo end-to-end at least twice: live camera feed →
fine-tuned density heatmap → motion coherence → forecast string → alert
firing on a deliberately induced compression scenario. Then leave the
container running and submit the link.
