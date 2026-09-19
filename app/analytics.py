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

    def process_frame(self, frame: np.ndarray) -> Dict[str, Any]:
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

        # Compute Density Proxy using adaptive thresholding and gradient intensity
        # People/heads create sharp high-frequency edges and contrast against the floor
        grad_x = cv2.Sobel(small_gray, cv2.CV_16S, 1, 0, ksize=3)
        grad_y = cv2.Sobel(small_gray, cv2.CV_16S, 0, 1, ksize=3)
        abs_grad = cv2.convertScaleAbs(grad_x) + cv2.convertScaleAbs(grad_y)
        _, density_mask = cv2.threshold(abs_grad, 40, 255, cv2.THRESH_BINARY)

        cell_h = small_h // self.rows
        cell_w = small_w // self.cols

        zones = []
        max_density = 0.0
        min_variance = 999.0
        max_risk_score = 0.0

        for r in range(self.rows):
            y1 = r * cell_h
            y2 = (r + 1) * cell_h
            for c in range(self.cols):
                x1 = c * cell_w
                x2 = (c + 1) * cell_w

                # 1. Density in cell (ratio of active crowd pixels)
                cell_density_px = np.count_nonzero(density_mask[y1:y2, x1:x2])
                density = min(1.0, float(cell_density_px) / (cell_h * cell_w * 0.45))
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

                # 3. Risk Fusion Rule
                # Compression Crush Signal: High Density + Variance Collapse (< 0.2 of baseline)
                is_high_density = density > 0.45
                variance_ratio = variance / (self.baseline_variance[r, c] + 1e-4)
                is_variance_collapse = variance_ratio < 0.25 and len(self.grid_history[r][c]) >= 6

                # Surge Signal: High Density + Extreme Directional Coherence
                is_surge = is_high_density and (coherence > 0.82) and (mean_mag > 1.2)

                # Severity level calculation
                if is_variance_collapse and density > 0.65:
                    level = "RED"
                    score = 0.95
                elif is_surge or (is_variance_collapse and density > 0.45):
                    level = "ORANGE"
                    score = 0.75
                elif is_high_density:
                    level = "YELLOW"
                    score = 0.50
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
                    "variance": round(variance, 3),
                    "coherence": round(coherence, 2),
                    "level": level,
                    "score": round(score, 2)
                })

        # Overall scene risk
        if max_risk_score >= 0.85:
            overall_level = "RED"
            overall_status = "CRITICAL: COMPRESSION CRUSH DETECTED"
        elif max_risk_score >= 0.70:
            overall_level = "ORANGE"
            overall_status = "WARNING: COMPRESSION PRECURSOR"
        elif max_risk_score >= 0.45:
            overall_level = "YELLOW"
            overall_status = "ELEVATED: HIGH DENSITY DETECTED"
        else:
            overall_level = "GREEN"
            overall_status = "NORMAL: SAFE DENSITY & DISPERSION"

        return {
            "overall_level": overall_level,
            "overall_status": overall_status,
            "max_risk_score": round(max_risk_score, 2),
            "max_density": round(max_density, 2),
            "min_variance": round(min_variance if min_variance < 900 else 1.0, 3),
            "zones": zones
        }
