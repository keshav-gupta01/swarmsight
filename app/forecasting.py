import time
import numpy as np
from collections import deque
from typing import Dict, List, Any, Optional

class ForecastEngine:
    """
    In-Memory Rolling Time-Series Predictive Forecasting Engine.
    Tracks per-zone density trajectories and extrapolates time-to-critical
    density thresholds before physical compression occurs.
    """
    def __init__(
        self,
        rows: int = 6,
        cols: int = 8,
        window_seconds: float = 30.0,
        critical_density_thresh: float = 0.60
    ):
        self.rows = rows
        self.cols = cols
        self.window_seconds = window_seconds
        self.critical_density_thresh = critical_density_thresh
        
        # history[zone_id] = deque of (timestamp, density, coherence)
        self.history: Dict[str, deque] = {
            f"zone_{r}_{c}": deque()
            for r in range(rows)
            for c in range(cols)
        }

    def reset(self):
        for zone_id in self.history:
            self.history[zone_id].clear()

    def update_and_forecast(self, zones: List[Dict[str, Any]], current_time: Optional[float] = None) -> List[Dict[str, Any]]:
        """
        Ingests the latest frame's zone telemetry, updates rolling history,
        and attaches predictive forecast metrics to each zone.
        """
        now = current_time if current_time is not None else time.time()
        earliest_critical_time: Optional[int] = None
        critical_zone_id: Optional[str] = None

        updated_zones = []
        for zone in zones:
            z_id = zone["id"]
            density = float(zone["density"])
            coherence = float(zone.get("coherence", 0.0))
            
            # Append current observation
            q = self.history[z_id]
            q.append((now, density, coherence))

            # Evict entries older than window_seconds
            cutoff = now - self.window_seconds
            while q and q[0][0] < cutoff:
                q.popleft()

            # Default forecast values
            forecast_seconds: Optional[int] = None
            forecast_label = "Stable"
            trend_direction = "flat"  # "rising", "falling", "flat"
            rate_per_minute = 0.0

            if len(q) >= 5:
                # Extract timestamps (normalized to start at 0) and densities
                t_arr = np.array([pt[0] - q[0][0] for pt in q], dtype=np.float32)
                d_arr = np.array([pt[1] for pt in q], dtype=np.float32)

                dt = t_arr[-1] - t_arr[0]
                if dt >= 2.0:  # Need at least 2 seconds of history for slope
                    # Linear regression slope: d(density) / dt (per second)
                    slope, _ = np.polyfit(t_arr, d_arr, deg=1)
                    rate_per_minute = round(float(slope * 60.0 * 100.0), 1)  # percentage points / min

                    if slope > 0.003:  # Density increasing (>0.3% per sec)
                        trend_direction = "rising"
                        if density >= self.critical_density_thresh:
                            forecast_label = "CRITICAL: Threshold Reached"
                            forecast_seconds = 0
                        else:
                            rem_density = self.critical_density_thresh - density
                            time_to_crit = rem_density / slope
                            if time_to_crit <= 300:  # Within 5 minutes
                                forecast_seconds = int(max(5, round(time_to_crit)))
                                forecast_label = f"⏱ ~{forecast_seconds}s to critical"
                                if earliest_critical_time is None or forecast_seconds < earliest_critical_time:
                                    earliest_critical_time = forecast_seconds
                                    critical_zone_id = z_id
                            else:
                                forecast_label = f"Rising slowly (+{rate_per_minute}%/min)"
                    elif slope < -0.003:
                        trend_direction = "falling"
                        forecast_label = "Dispersing"
                    else:
                        trend_direction = "flat"
                        if density >= self.critical_density_thresh:
                            forecast_label = "CRITICAL: High Static Density"
                            forecast_seconds = 0
                        else:
                            forecast_label = "Stable"
            elif density >= self.critical_density_thresh:
                forecast_label = "CRITICAL: High Density"
                forecast_seconds = 0

            # Augment zone dictionary
            z_copy = dict(zone)
            z_copy["forecast_seconds"] = forecast_seconds
            z_copy["forecast_label"] = forecast_label
            z_copy["trend_direction"] = trend_direction
            z_copy["rate_per_minute"] = rate_per_minute
            updated_zones.append(z_copy)

        return updated_zones
