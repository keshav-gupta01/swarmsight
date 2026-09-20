import asyncio
import base64
import json
import os
import time
import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.analytics import CrowdAnalyticsEngine
from app.alerting import AlertManager

app = FastAPI(title="SwarmSight Live Analytics & Alerting Server")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
CLIPS_DIR = "/opt/swarmsight/data/clips"

if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

CLIPS = {
    # Real crowd videos (Primary active demo)
    "safe": os.path.join(CLIPS_DIR, "SAFE.mp4"),
    "compression": os.path.join(CLIPS_DIR, "UNSAFE.mp4"),
    "real_safe": os.path.join(CLIPS_DIR, "SAFE.mp4"),
    "real_unsafe": os.path.join(CLIPS_DIR, "UNSAFE.mp4"),
    # Original simulation videos (Revert / Fallback options)
    "sim_safe": os.path.join(CLIPS_DIR, "safe_crowd.mp4"),
    "sim_compression": os.path.join(CLIPS_DIR, "compression_crowd.mp4")
}

current_clip = "safe"
latest_live_frame = None
latest_live_timestamp = 0.0

alert_manager = AlertManager(sns_cooldown_seconds=3600.0, db_cooldown_seconds=60.0)

class LiveFrameUpload(BaseModel):
    frame: str  # Base64 encoded JPEG
    source: str = "webcam"

@app.get("/")
def get_index():
    index_file = os.path.join(STATIC_DIR, "index.html")
    return FileResponse(index_file)

@app.get("/api/select_feed/{feed_id}")
def select_feed(feed_id: str):
    global current_clip
    if feed_id == "live" or feed_id in CLIPS:
        current_clip = feed_id
        return {"status": "ok", "feed": feed_id}
    return JSONResponse(status_code=400, content={"status": "error", "message": "Unknown feed"})

@app.post("/api/live_frame")
def upload_live_frame(payload: LiveFrameUpload):
    global latest_live_frame, latest_live_timestamp, current_clip
    try:
        data = payload.frame
        if "," in data:
            data = data.split(",", 1)[1]
        raw_bytes = base64.b64decode(data)
        nparr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is not None:
            latest_live_frame = img
            latest_live_timestamp = time.time()
            current_clip = "live"
            return {"status": "ok", "timestamp": latest_live_timestamp}
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    return JSONResponse(status_code=400, content={"status": "error", "message": "Failed to decode image"})

@app.get("/api/alerts")
def get_alerts():
    """Returns recent alerts for the dashboard feed."""
    return {"alerts": alert_manager.get_recent_alerts()}

@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    global latest_live_frame, latest_live_timestamp, current_clip
    await websocket.accept()
    cap = None
    last_clip = None
    engine = CrowdAnalyticsEngine(rows=6, cols=8)

    # Receiver task to accept live camera frames from client browser
    async def frame_receiver():
        global latest_live_frame, latest_live_timestamp, current_clip
        try:
            while True:
                msg = await websocket.receive_text()
                try:
                    data = json.loads(msg)
                    if data.get("type") == "live_frame":
                        b64_str = data.get("frame", "")
                        if "," in b64_str:
                            b64_str = b64_str.split(",", 1)[1]
                        raw_bytes = base64.b64decode(b64_str)
                        nparr = np.frombuffer(raw_bytes, np.uint8)
                        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                        if img is not None:
                            latest_live_frame = img
                            latest_live_timestamp = time.time()
                            current_clip = "live"
                    elif data.get("type") == "select_feed":
                        f_id = data.get("feed")
                        if f_id == "live" or f_id in CLIPS:
                            current_clip = f_id
                except Exception:
                    pass
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            pass

    receiver_task = asyncio.create_task(frame_receiver())

    try:
        while True:
            frame = None
            active_feed = current_clip

            # Check if Live Camera feed is active and received recently
            if current_clip == "live":
                if latest_live_frame is not None and (time.time() - latest_live_timestamp) < 3.0:
                    frame = latest_live_frame.copy()
                    active_feed = "live_camera"
                else:
                    # Seamless fallback to demo video loop if camera drops
                    active_feed = "safe"

            if frame is None:
                # Video file loop fallback
                if cap is None or not cap.isOpened() or active_feed != last_clip:
                    if cap is not None:
                        cap.release()
                    last_clip = active_feed
                    clip_path = CLIPS.get(active_feed, CLIPS["safe"])
                    cap = cv2.VideoCapture(clip_path)
                    engine.reset()

                ret, frame = cap.read()
                if not ret:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue

            # Run Crowd Analytics (Density + Optical Flow + Risk Fusion + Forecasting)
            telemetry = engine.process_frame(frame, feed=active_feed)

            # Check Alert Conditions (DynamoDB + SNS on Orange/Red)
            new_alerts = alert_manager.check_and_alert(telemetry, active_feed)

            # Encode frame to JPEG
            _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            b64_frame = base64.b64encode(buffer).decode('utf-8')

            # Send frame + analytics telemetry + alert payload
            await websocket.send_json({
                "feed": active_feed,
                "frame": b64_frame,
                "telemetry": telemetry,
                "new_alerts": new_alerts,
                "recent_alerts": alert_manager.get_recent_alerts()[:10]
            })

            # Stream at ~7-8 fps
            await asyncio.sleep(0.12)
    except WebSocketDisconnect:
        pass
    finally:
        receiver_task.cancel()
        if cap is not None:
            cap.release()
