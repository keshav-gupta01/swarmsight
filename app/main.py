import asyncio
import base64
import os
import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from app.analytics import CrowdAnalyticsEngine
from app.alerting import AlertManager

app = FastAPI(title="SwarmSight Live Analytics & Alerting Server")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
CLIPS_DIR = "/opt/swarmsight/data/clips"

if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

CLIPS = {
    "safe": os.path.join(CLIPS_DIR, "safe_crowd.mp4"),
    "compression": os.path.join(CLIPS_DIR, "compression_crowd.mp4")
}

current_clip = "safe"
alert_manager = AlertManager(sns_cooldown_seconds=120.0, db_cooldown_seconds=30.0)

@app.get("/")
def get_index():
    index_file = os.path.join(STATIC_DIR, "index.html")
    return FileResponse(index_file)

@app.get("/api/select_feed/{feed_id}")
def select_feed(feed_id: str):
    global current_clip
    if feed_id in CLIPS:
        current_clip = feed_id
        return {"status": "ok", "feed": feed_id}
    return JSONResponse(status_code=400, content={"status": "error", "message": "Unknown feed"})

@app.get("/api/alerts")
def get_alerts():
    """Returns recent alerts for the dashboard feed."""
    return {"alerts": alert_manager.get_recent_alerts()}

@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    cap = None
    last_clip = None
    engine = CrowdAnalyticsEngine(rows=6, cols=8)

    try:
        while True:
            # Re-open if feed switched or loop ended
            if cap is None or not cap.isOpened() or current_clip != last_clip:
                if cap is not None:
                    cap.release()
                last_clip = current_clip
                clip_path = CLIPS.get(current_clip, CLIPS["safe"])
                cap = cv2.VideoCapture(clip_path)
                engine.reset()

            ret, frame = cap.read()
            if not ret:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue

            # Run Crowd Analytics (Density + Farneback Flow + Risk Fusion)
            telemetry = engine.process_frame(frame)

            # Check Alert Conditions (DynamoDB + SNS on Orange/Red)
            new_alerts = alert_manager.check_and_alert(telemetry, current_clip)

            # Encode frame to JPEG
            _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            b64_frame = base64.b64encode(buffer).decode('utf-8')

            # Send frame + analytics telemetry + alert payload
            await websocket.send_json({
                "feed": current_clip,
                "frame": b64_frame,
                "telemetry": telemetry,
                "new_alerts": new_alerts,
                "recent_alerts": alert_manager.get_recent_alerts()[:10]
            })

            # Stream at ~7 fps
            await asyncio.sleep(0.14)
    except WebSocketDisconnect:
        pass
    finally:
        if cap is not None:
            cap.release()
