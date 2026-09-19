import os
import time
import logging
from decimal import Decimal
from typing import Dict, List, Any
import boto3

logger = logging.getLogger("swarmsight.alerting")
logging.basicConfig(level=logging.INFO)

REGION = os.environ.get("AWS_REGION", "ap-south-1")
DYNAMO_TABLE_NAME = os.environ.get("SWARMSIGHT_TABLE", "swarmsight_alerts")
SNS_TOPIC_ARN = os.environ.get(
    "SWARMSIGHT_SNS_ARN",
    "arn:aws:sns:ap-south-1:879268673611:swarmsight-alerts"
)

class AlertManager:
    """
    Manages stateful alert debouncing, DynamoDB persistence,
    and throttled SNS email notifications on Orange/Red crowd risk transitions.
    
    Anti-Spam Guarantees:
    1. SNS Cooldown: Maximum 1 email every 120 seconds across the entire app.
    2. Incident Aggregation: Consolidates multiple zone alarms into a single summary email.
    3. State Escalation: Only emails on transition to elevated danger, not repetitive loops.
    """
    def __init__(self, sns_cooldown_seconds: float = 3600.0, db_cooldown_seconds: float = 60.0, **kwargs):
        # Support legacy cooldown_seconds if passed
        if "cooldown_seconds" in kwargs:
            sns_cooldown_seconds = kwargs["cooldown_seconds"]
        self.sns_cooldown_seconds = sns_cooldown_seconds
        self.db_cooldown_seconds = db_cooldown_seconds
        
        self.last_sns_sent_time = 0.0
        self.last_scene_level = "GREEN"
        self.last_db_write_time = {}   # zone_id -> timestamp
        self.recent_alerts = []        # in-memory buffer for UI feed (max 50)
        
        # Initialize AWS clients using EC2 instance profile
        try:
            self.dynamodb = boto3.resource('dynamodb', region_name=REGION)
            self.table = self.dynamodb.Table(DYNAMO_TABLE_NAME)
            self.sns = boto3.client('sns', region_name=REGION)
            self.aws_ready = True
            logger.info("AlertManager AWS clients initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize AWS clients: {e}")
            self.aws_ready = False

    def check_and_alert(self, telemetry: Dict[str, Any], feed_name: str) -> List[Dict[str, Any]]:
        """
        Evaluates current telemetry.
        - Updates local UI alert feed.
        - Throttles DynamoDB writes (max 1 per zone every 30s).
        - Strictly throttles SNS email to max 1 aggregated email per 120 seconds.
        """
        now = time.time()
        new_ui_alerts = []

        overall_level = telemetry.get("overall_level", "GREEN")
        zones = telemetry.get("zones", [])

        # Find all critical/warning zones
        alert_zones = [z for z in zones if z.get("level") in ("ORANGE", "RED")]

        if not alert_zones:
            self.last_scene_level = overall_level
            return []

        # Find the single most critical zone in the frame
        worst_zone = max(alert_zones, key=lambda z: (1 if z["level"] == "RED" else 0, z.get("density", 0)))
        worst_id = worst_zone.get("id")
        worst_level = worst_zone.get("level")
        density_pct = int(worst_zone.get("density", 0) * 100)
        variance = worst_zone.get("variance", 0)
        coherence = worst_zone.get("coherence", 0)

        # Build human-readable reason
        if worst_level == "RED":
            reason = (
                f"CRITICAL COMPRESSION CRUSH in sector {worst_id} ({feed_name.upper()}): "
                f"Density at {density_pct}% with micro-motion variance collapse to {variance}."
            )
        else:
            reason = (
                f"PRECURSOR WARNING in sector {worst_id} ({feed_name.upper()}): "
                f"Density at {density_pct}% with directional coherence {coherence}."
            )

        # 1. Update UI Alerts Feed (debounced per zone every 10s for UI display)
        last_ui_time = self.last_db_write_time.get(f"ui_{worst_id}", 0)
        if (now - last_ui_time) > 10.0:
            self.last_db_write_time[f"ui_{worst_id}"] = now
            ui_alert = {
                "zone_id": worst_id,
                "timestamp": int(now),
                "feed": feed_name,
                "risk_level": worst_level,
                "density": float(worst_zone.get("density", 0)),
                "variance": float(variance),
                "coherence": float(coherence),
                "reason": reason
            }
            new_ui_alerts.append(ui_alert)
            self.recent_alerts.insert(0, ui_alert)
            if len(self.recent_alerts) > 50:
                self.recent_alerts.pop()

        # 2. Persist to DynamoDB (throttled to max 1 write every 30s per zone)
        last_db_time = self.last_db_write_time.get(worst_id, 0)
        if (now - last_db_time) > self.db_cooldown_seconds and self.aws_ready:
            self.last_db_write_time[worst_id] = now
            try:
                self.table.put_item(Item={
                    "zone_id": worst_id,
                    "timestamp": int(now),
                    "feed": feed_name,
                    "risk_level": worst_level,
                    "density": Decimal(str(worst_zone.get("density", 0))),
                    "variance": Decimal(str(variance)),
                    "coherence": Decimal(str(coherence)),
                    "reason": reason,
                    "ttl": int(now) + (86400 * 7)
                })
                logger.info(f"DynamoDB logged: {worst_id} [{worst_level}]")
            except Exception as e:
                logger.error(f"DynamoDB PutItem error: {e}")

        # 3. SNS Email Dispatch (STRICT ANTI-SPAM: Max 1 email every 120s, only on escalation or major incident)
        time_since_last_email = now - self.last_sns_sent_time
        is_scene_escalation = (self.last_scene_level in ("GREEN", "YELLOW") and overall_level in ("ORANGE", "RED"))
        is_red_escalation = (self.last_scene_level == "ORANGE" and overall_level == "RED")

        if (time_since_last_email > self.sns_cooldown_seconds) and (is_scene_escalation or is_red_escalation or time_since_last_email > 300):
            self.last_sns_sent_time = now
            self.last_scene_level = overall_level
            self._send_consolidated_email(
                feed_name=feed_name,
                overall_level=overall_level,
                affected_zones=[z["id"] for z in alert_zones],
                worst_zone=worst_zone,
                reason=reason,
                timestamp=int(now)
            )
        else:
            self.last_scene_level = overall_level

        return new_ui_alerts

    def _send_consolidated_email(self, feed_name: str, overall_level: str, affected_zones: List[str], worst_zone: Dict[str, Any], reason: str, timestamp: int):
        """Sends a single consolidated incident alert email to the SNS topic."""
        if not self.aws_ready or not SNS_TOPIC_ARN:
            return

        try:
            subject = f"⚠️ SwarmSight {overall_level} Alert — Drone {feed_name.upper()} ({len(affected_zones)} zones affected)"
            message = (
                f"SWARMSIGHT REAL-TIME CROWD INCIDENT DISPATCH\n"
                f"===============================================\n"
                f"Severity Status:    {overall_level}\n"
                f"Drone Feed:         {feed_name.upper()}\n"
                f"Sectors Affected:   {', '.join(affected_zones[:6])}\n"
                f"Primary Epicenter:  {worst_zone.get('id')}\n"
                f"Time:               {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(timestamp))}\n"
                f"\n"
                f"INCIDENT SUMMARY:\n"
                f"{reason}\n"
                f"\n"
                f"KEY METRICS AT EPICENTER:\n"
                f"- Crowd Density:             {int(worst_zone.get('density', 0) * 100)}%\n"
                f"- Micro-Motion Variance:     {worst_zone.get('variance', 0)} (Threshold < 0.25 indicates physical crush freeze)\n"
                f"- Flow Direction Coherence:  {worst_zone.get('coherence', 0)}\n"
                f"\n"
                f"NEXT ACTION:\n"
                f"Deploy ground security to relieve exit bottlenecks at sector {worst_zone.get('id')}.\n"
                f"\n"
                f"---\n"
                f"Rate limit notice: SwarmSight automatically throttles notifications to prevent spam (cooldown: 2 min).\n"
            )
            self.sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject=subject[:100],
                Message=message
            )
            logger.info(f"Consolidated SNS Email dispatched for {len(affected_zones)} zones.")
        except Exception as e:
            logger.error(f"Failed to publish SNS email: {e}")

    def get_recent_alerts(self) -> List[Dict[str, Any]]:
        return self.recent_alerts
