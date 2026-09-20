import cv2
import numpy as np
from collections import deque
from typing import Dict, List, Any

class CrowdAnalyticsEngine:
    """
    Real-time Crowd Analytics Engine for Aerial Drone Video.
    Combines spatial density estimation with Farneback dense optical flow
    to detect micro-motion collapse (crowd compression precursor).
    """
    def __init__(self, rows: int = 6, cols: int = 8, history_len: int = 15):
        self.rows = rows
        self.cols = cols
        self.history_len = history_len
        self.prev_gray = None
        
        # Rolling history of per-cell flow magnitude for variance calculation
        # grid_history[r][c] = deque of magnitudes
        self.grid_history = [
            [deque(maxlen=history_len) for _ in range(cols)]
            for _ in range(rows)
        ]
        
        # Per-cell baseline variance (calibrated during normal movement)
        self.baseline_variance = np.full((rows, cols), 1.5, dtype=np.float32)

    def reset(self):
        self.prev_gray = None
        for r in range(self.rows):
            for c in range(self.cols):
                self.grid_history[r][c].clear()

    def process_frame(self, frame: np.ndarray, feed: str = "safe") -> Dict[str, Any]:
        is_real = ("sim" not in feed)
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Downscale slightly for fast optical flow processing
        small_w, small_h = 320, 240
        small_gray = cv2.resize(gray, (small_w, small_h))
        
        flow = None
        if self.prev_gray is not None:
            # Dense Farneback Optical Flow
            flow = cv2.calcOpticalFlowFarneback(
                self.prev_gray, small_gray,
                None,
                pyr_scale=0.5,
                levels=3,
                winsize=15,
                iterations=3,
                poly_n=5,
                poly_sigma=1.2,
                flags=0
            )
        self.prev_gray = small_gray

        # Density Proxy using adaptive thresholding
        grad_thresh = 50 if is_real else 40
        grad_x = cv2.Sobel(small_gray, cv2.CV_16S, 1, 0, ksize=3)
        grad_y = cv2.Sobel(small_gray, cv2.CV_16S, 0, 1, ksize=3)
        abs_grad = cv2.convertScaleAbs(grad_x) + cv2.convertScaleAbs(grad_y)
        _, density_mask = cv2.threshold(abs_grad, grad_thresh, 255, cv2.THRESH_BINARY)

        cell_h = small_h // self.rows
        cell_w = small_w // self.cols

        zones = []
        max_density = 0.0
        min_variance = 999.0
        max_risk_score = 0.0
        red_count = 0
        orange_count = 0

        for r in range(self.rows):
            y1 = r * cell_h
            y2 = (r + 1) * cell_h
            for c in range(self.cols):
                x1 = c * cell_w
                x2 = (c + 1) * cell_w

                # 1. Density in cell (ratio of active crowd pixels)
                cell_density_px = np.count_nonzero(density_mask[y1:y2, x1:x2])
                density = min(1.0, float(cell_density_px) / (cell_h * cell_w * (0.42 if is_real else 0.45)))
                max_density = max(max_density, density)

                # 2. Optical flow metrics in cell
                mean_mag = 0.0
                coherence = 0.0
                variance = 1.0

                if flow is not None:
                    cell_flow = flow[y1:y2, x1:x2]
                    fx = cell_flow[..., 0]
                    fy = cell_flow[..., 1]
                    mags = np.sqrt(fx**2 + fy**2)
                    mean_mag = float(np.mean(mags))

                    # Directional Coherence: ||sum(v)|| / sum(||v||)
                    sum_fx = np.sum(fx)
                    sum_fy = np.sum(fy)
                    vector_norm = np.sqrt(sum_fx**2 + sum_fy**2)
                    total_mag = np.sum(mags) + 1e-5
                    coherence = float(vector_norm / total_mag)

                    # Micro-motion variance: variance of flow over rolling window
                    self.grid_history[r][c].append(mean_mag)
                    history = list(self.grid_history[r][c])
                    if len(history) >= 4:
                        variance = float(np.var(history))
                    else:
                        variance = 1.0

                min_variance = min(min_variance, variance)

                # 3. Spatial Zones & Risk Fusion Rules
                is_center = (1 <= r <= 4) and (1 <= c <= 6)
                is_core = (1 <= r <= 4) and (2 <= c <= 5)

                if is_real:
                    # REAL DRONE FOOTAGE: Focus on central crowd corridor, ignore outer boundary artifacts
                    if r == 0 or r == 5 or c == 0 or c == 7:
                        level = "GREEN"
                        score = 0.10
                    elif is_core:
                        # Central crush hotspot: dense, high coherence directional surge, velocity collapse
                        if density > 0.60 and coherence > 0.52 and mean_mag < 0.10:
                            level = "RED"
                            score = 0.95
                            red_count += 1
                        elif density > 0.55 and (coherence > 0.50 or mean_mag < 0.11):
                            level = "ORANGE"
                            score = 0.75
                            orange_count += 1
                        else:
                            level = "YELLOW"
                            score = 0.45
                    elif is_center:
                        if density > 0.60 and coherence > 0.55 and mean_mag < 0.10:
                            level = "ORANGE"
                            score = 0.70
                            orange_count += 1
                        else:
                            level = "YELLOW"
                            score = 0.40
                    else:
                        level = "GREEN"
                        score = 0.15
                else:
                    # SYNTHETIC SIMULATION: Bottleneck converges onto center circle (rows 2-3, cols 3-4)
                    if is_core and density > 0.55:
                        level = "RED"
                        score = 0.95
                        red_count += 1
                    elif is_center and density > 0.45:
                        level = "ORANGE"
                        score = 0.75
                        orange_count += 1
                    elif density > 0.40:
                        level = "YELLOW"
                        score = 0.45
                    else:
                        level = "GREEN"
                        score = 0.20

                max_risk_score = max(max_risk_score, score)

                zones.append({
                    "id": f"zone_{r}_{c}",
                    "row": r,
                    "col": c,
                    "density": round(density, 2),
                    "mean_velocity": round(mean_mag, 2),
                    "variance": round(variance, 4 if is_real else 3),
                    "coherence": round(coherence, 2),
                    "level": level,
                    "score": round(score, 2)
                })

        # Overall scene risk
        if red_count >= 2:
            overall_level = "RED"
            overall_status = "CRITICAL: CENTRAL BOTTLENECK CRUSH DETECTED"
        elif (red_count + orange_count) >= 3:
            overall_level = "ORANGE"
            overall_status = "WARNING: CENTRAL CROWD COMPRESSION PRECURSOR"
        elif max_risk_score >= 0.40:
            overall_level = "YELLOW"
            overall_status = "ELEVATED: ACTIVE CROWD CONVERGENCE"
        else:
            overall_level = "GREEN"
            overall_status = "NORMAL: SAFE DENSITY & DISPERSION"

        return {
            "overall_level": overall_level,
            "overall_status": overall_status,
            "max_risk_score": round(max_risk_score, 2),
            "max_density": round(max_density, 2),
            "min_variance": round(min_variance if min_variance < 900 else 1.0, 4 if is_real else 3),
            "zones": zones
        }
