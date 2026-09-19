import os
import time
import json
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
    and SNS notifications on Orange/Red crowd risk transitions.
    """
    def __init__(self, cooldown_seconds: float = 15.0):
        self.cooldown_seconds = cooldown_seconds
        self.zone_states = {}       # zone_id -> {"level": str, "last_alert": float}
        self.recent_alerts = []     # in-memory buffer for instant UI feed (max 50)
        
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
        Evaluates current telemetry. If any zone transitions to ORANGE or RED
        and cooldown has elapsed, writes to DynamoDB and dispatches SNS.
        Returns list of newly fired alerts.
        """
        now = time.time()
        new_alerts = []

        # 1. Check individual zones that crossed threshold
        for zone in telemetry.get("zones", []):
            level = zone.get("level")
            zone_id = zone.get("id")

            # Only trigger on ORANGE or RED
            if level not in ("ORANGE", "RED"):
                # Reset state so it can re-trigger next time it escalates
                if zone_id in self.zone_states and self.zone_states[zone_id]["level"] in ("ORANGE", "RED"):
                    self.zone_states[zone_id]["level"] = level
                continue

            prev_info = self.zone_states.get(zone_id, {"level": "GREEN", "last_alert": 0})
            prev_level = prev_info["level"]
            last_alert_time = prev_info["last_alert"]

            # Trigger condition:
            # - Escalation (e.g. GREEN/YELLOW -> ORANGE, or ORANGE -> RED)
            # - OR cooldown expired while still in ORANGE/RED state
            is_escalation = (prev_level != level)
            cooldown_expired = (now - last_alert_time) > self.cooldown_seconds

            if is_escalation or cooldown_expired:
                # Build explainability description
                density_pct = int(zone.get("density", 0) * 100)
                variance = zone.get("variance", 0)
                coherence = zone.get("coherence", 0)

                if level == "RED":
                    reason = (
                        f"CRITICAL COMPRESSION CRUSH HAZARD in {zone_id} ({feed_name.upper()}): "
                        f"Density at {density_pct}% with micro-motion variance collapse to {variance}."
                    )
                else:
                    reason = (
                        f"PRECURSOR WARNING in {zone_id} ({feed_name.upper()}): "
                        f"High density ({density_pct}%) and elevated directional coherence ({coherence})."
                    )

                alert_record = {
                    "zone_id": zone_id,
                    "timestamp": int(now),
                    "feed": feed_name,
                    "risk_level": level,
                    "density": Decimal(str(zone.get("density", 0))),
                    "variance": Decimal(str(variance)),
                    "coherence": Decimal(str(coherence)),
                    "reason": reason,
                    "ttl": int(now) + (86400 * 7) # 7-day DynamoDB TTL
                }

                # Update state
                self.zone_states[zone_id] = {
                    "level": level,
                    "last_alert": now
                }

                # Dispatch async / non-blocking
                self._dispatch_alert(alert_record)

                # Format for JSON serialization in telemetry
                json_record = {
                    "zone_id": zone_id,
                    "timestamp": int(now),
                    "feed": feed_name,
                    "risk_level": level,
                    "density": float(zone.get("density", 0)),
                    "variance": float(variance),
                    "coherence": float(coherence),
                    "reason": reason
                }
                new_alerts.append(json_record)
                self.recent_alerts.insert(0, json_record)
                if len(self.recent_alerts) > 50:
                    self.recent_alerts.pop()

        return new_alerts

    def _dispatch_alert(self, record: Dict[str, Any]):
        """Persists alert record to DynamoDB and publishes to SNS."""
        # 1. DynamoDB
        if self.aws_ready:
            try:
                self.table.put_item(Item=record)
                logger.info(f"Alert written to DynamoDB: {record['zone_id']} - {record['risk_level']}")
            except Exception as e:
                logger.error(f"DynamoDB PutItem failed: {e}")

        # 2. SNS Notification (SMS / Email)
        if self.aws_ready and SNS_TOPIC_ARN:
            try:
                subject = f"⚠️ SwarmSight {record['risk_level']} Alert — {record['zone_id']}"
                message = (
                    f"SWARMSIGHT REAL-TIME CROWD SAFETY ALERT\n"
                    f"=========================================\n"
                    f"Severity Level: {record['risk_level']}\n"
                    f"Sector:         {record['zone_id']}\n"
                    f"Drone Feed:     {record['feed']}\n"
                    f"Timestamp:      {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(int(record['timestamp'])))}\n"
                    f"\n"
                    f"Root Cause Analysis:\n"
                    f"{record['reason']}\n"
                    f"\n"
                    f"Metrics:\n"
                    f"- Crowd Density:       {record['density']}\n"
                    f"- Micro-Motion Var:    {record['variance']}\n"
                    f"- Flow Coherence:      {record['coherence']}\n"
                    f"\n"
                    f"Action Required: Direct ground stewards to relieve bottleneck.\n"
                )
                self.sns.publish(
                    TopicArn=SNS_TOPIC_ARN,
                    Subject=subject[:100],
                    Message=message
                )
                logger.info(f"SNS notification published for {record['zone_id']}")
            except Exception as e:
                logger.error(f"SNS Publish failed: {e}")

    def get_recent_alerts(self) -> List[Dict[str, Any]]:
        return self.recent_alerts
