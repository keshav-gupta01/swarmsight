---
trigger: always_on
---

# SwarmSight AWS Rules

Use only:

- S3 for videos and snapshots
- One EC2 instance for FastAPI, WebSocket server, and frontend
- IAM instance profile for EC2
- DynamoDB table swarmsight_alerts
- SNS for Orange and Red alerts

Rules:

- Never hardcode AWS credentials.
- Use the EC2 instance profile in production.
- Use least-privilege IAM.
- Keep S3 private.
- Do not deploy or modify live AWS resources without approval.
- Do not add Lambda, ECS, RDS, CloudFront, Cognito, SQS, or other services without explaining why.
- Ask before any create, update, delete, or deploy operation.