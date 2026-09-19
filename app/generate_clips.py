import os
import cv2
import numpy as np

def generate():
    os.makedirs('/opt/swarmsight/data/clips', exist_ok=True)
    width, height = 640, 480
    fps = 10
    duration_sec = 10
    total_frames = fps * duration_sec
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')

    # 1. Safe Crowd Clip (Distributed moving dots)
    print("Generating safe_crowd.mp4...")
    safe_path = '/opt/swarmsight/data/clips/safe_crowd.mp4'
    out_safe = cv2.VideoWriter(safe_path, fourcc, fps, (width, height))
    np.random.seed(42)
    num_people = 120
    pos = np.random.rand(num_people, 2) * [width, height]

    for f in range(total_frames):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (30, 35, 40)
        for x in range(0, width, 80):
            cv2.line(frame, (x, 0), (x, height), (45, 50, 55), 1)
        for y in range(0, height, 80):
            cv2.line(frame, (0, y), (width, y), (45, 50, 55), 1)
        
        pos += (np.random.rand(num_people, 2) - 0.5) * 6
        pos[:, 0] = np.clip(pos[:, 0], 20, width - 20)
        pos[:, 1] = np.clip(pos[:, 1], 20, height - 20)
        
        for p in pos:
            cv2.circle(frame, (int(p[0]), int(p[1])), 5, (100, 200, 100), -1)
        
        cv2.putText(frame, f"FEED: DRONE-ALPHA | SAFE DISPERSION | FRAME {f:03d}", (15, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 255, 200), 2)
        out_safe.write(frame)
    out_safe.release()

    # 2. Compression Precursor Clip (Crowd converging and locking up)
    print("Generating compression_crowd.mp4...")
    crush_path = '/opt/swarmsight/data/clips/compression_crowd.mp4'
    out_crush = cv2.VideoWriter(crush_path, fourcc, fps, (width, height))
    num_people = 350
    pos = np.random.rand(num_people, 2) * [width, height]
    target = np.array([width // 2, height // 2])

    for f in range(total_frames):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (30, 35, 40)
        cv2.circle(frame, (width // 2, height // 2), 90, (40, 40, 80), 2)
        
        convergence_speed = min(1.0, f / 40.0)
        direction = target - pos
        norm = np.linalg.norm(direction, axis=1, keepdims=True) + 1e-5
        
        dist_to_center = np.linalg.norm(pos - target, axis=1)
        frozen_mask = (dist_to_center < 80) & (f > 40)
        
        pos += (direction / norm) * 3.5 * convergence_speed
        pos[frozen_mask] += (np.random.rand(np.sum(frozen_mask), 2) - 0.5) * 0.5
        pos[:, 0] = np.clip(pos[:, 0], 10, width - 10)
        pos[:, 1] = np.clip(pos[:, 1], 10, height - 10)
        
        for i, p in enumerate(pos):
            color = (0, 0, 240) if frozen_mask[i] else (50, 180, 240)
            cv2.circle(frame, (int(p[0]), int(p[1])), 4, color, -1)
            
        status = "WARNING: HIGH DENSITY COMPRESSION" if f > 40 else "STATUS: GATHERING"
        color = (0, 100, 255) if f > 40 else (200, 255, 200)
        cv2.putText(frame, f"FEED: DRONE-BRAVO | {status} | FRAME {f:03d}", (15, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        out_crush.write(frame)
    out_crush.release()
    print("Demo clips generated successfully at /opt/swarmsight/data/clips/")

if __name__ == '__main__':
    generate()
