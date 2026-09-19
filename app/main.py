import asyncio
import base64
import os
import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.analytics import CrowdAnalyticsEngine

app = FastAPI(title="SwarmSight Live Analytics Server")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
CLIPS_DIR = "/opt/swarmsight/data/clips"

if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

CLIPS = {
    "safe": os.path.join(CLIPS_DIR, "safe_crowd.mp4"),
    "compression": os.path.join(CLIPS_DIR, "compression_crowd.mp4")
}

current_clip = "safe"

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
    return {"status": "error", "message": "Unknown feed"}, 400

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

            # Run Crowd Analytics (Density + Farneback Optical Flow + Risk Fusion)
            telemetry = engine.process_frame(frame)

            # Encode frame to JPEG
            _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            b64_frame = base64.b64encode(buffer).decode('utf-8')

            # Send frame + analytics telemetry
            await websocket.send_json({
                "feed": current_clip,
                "frame": b64_frame,
                "telemetry": telemetry
            })

            # Stream at ~7 fps
            await asyncio.sleep(0.14)
    except WebSocketDisconnect:
        pass
    finally:
        if cap is not None:
            cap.release()
