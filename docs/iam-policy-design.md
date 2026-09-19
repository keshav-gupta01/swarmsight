# SwarmSight — IAM Policy Design

> **Principle:** Least privilege by construction. The EC2 instance may only
> touch three named resources (one S3 bucket, one DynamoDB table, one SNS
> topic) and no AWS management plane. No credentials are stored on disk —
> the instance profile supplies short-lived STS credentials automatically
> via the Instance Metadata Service (IMSv2).

---

## IAM Role: `swarmsight-ec2-role`

```
Trust policy principal: ec2.amazonaws.com
```

This role is attached to the EC2 instance as an **instance profile**.
boto3 / the AWS SDK automatically fetches temporary credentials from
`http://169.254.169.254/latest/meta-data/iam/security-credentials/swarmsight-ec2-role`
using IMDSv2. No `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` is ever
set in the environment or on disk.

---

## Trust Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

---

## Inline Policy: `swarmsight-app-policy`

Replace the placeholders before creating:
- `<ACCOUNT_ID>` → `879268673611`
- `<BUCKET_NAME>` → your actual bucket name, e.g. `swarmsight-demo-kesha`

```json
{
  "Version": "2012-10-17",
  "Statement": [

    // ── S3: read clips, write optional snapshots ──────────────────────
    {
      "Sid": "S3ListBucket",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::<BUCKET_NAME>",
      "Condition": {
        "StringLike": {
          "s3:prefix": ["clips/*", "snapshots/*"]
        }
      }
    },
    {
      "Sid": "S3ReadClips",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::<BUCKET_NAME>/clips/*"
    },
    {
      "Sid": "S3WriteSnapshots",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::<BUCKET_NAME>/snapshots/*"
    },

    // ── DynamoDB: write alerts, query dashboard ───────────────────────
    {
      "Sid": "DynamoDBAlerts",
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:Query",
        "dynamodb:Scan"
      ],
      "Resource": "arn:aws:dynamodb:ap-south-1:<ACCOUNT_ID>:table/swarmsight_alerts"
    },

    // ── SNS: publish Orange/Red alerts only ───────────────────────────
    {
      "Sid": "SNSPublishAlerts",
      "Effect": "Allow",
      "Action": "sns:Publish",
      "Resource": "arn:aws:sns:ap-south-1:<ACCOUNT_ID>:swarmsight-alerts"
    }

  ]
}
```

> NOTE: JSON does not support `//` comments. Remove all comment lines
> before pasting this policy into the AWS Console or CLI.

---

## Clean JSON (comments removed — paste-ready)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3ListBucket",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::swarmsight-demo-kesha",
      "Condition": {
        "StringLike": {
          "s3:prefix": ["clips/*", "snapshots/*"]
        }
      }
    },
    {
      "Sid": "S3ReadClips",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::swarmsight-demo-kesha/clips/*"
    },
    {
      "Sid": "S3WriteSnapshots",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::swarmsight-demo-kesha/snapshots/*"
    },
    {
      "Sid": "DynamoDBAlerts",
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:Query",
        "dynamodb:Scan"
      ],
      "Resource": "arn:aws:dynamodb:ap-south-1:879268673611:table/swarmsight_alerts"
    },
    {
      "Sid": "SNSPublishAlerts",
      "Effect": "Allow",
      "Action": "sns:Publish",
      "Resource": "arn:aws:sns:ap-south-1:879268673611:swarmsight-alerts"
    }
  ]
}
```

---

## Permission Decisions — Action by Action

### S3

| Action | Granted | Why | Why NOT broader |
|---|---|---|---|
| `s3:ListBucket` | Yes, with prefix condition | App needs to check if a clip exists before streaming | Without prefix condition, would expose entire bucket listing |
| `s3:GetObject` on `clips/*` | Yes | App streams clip frames for inference | Scoped to `clips/` only — cannot read `snapshots/` or other prefixes |
| `s3:PutObject` on `snapshots/*` | Yes | Optional: write alert frame grids for post-review | Scoped to `snapshots/` only — cannot overwrite clips |
| `s3:DeleteObject` | **No** | App never needs to delete files | Prevents accidental or malicious clip deletion |
| `s3:GetBucketPolicy`, `s3:PutBucketPolicy` | **No** | Management plane — app has no need | Prevents policy modification from inside the instance |
| `s3:*` wildcard | **No** | Violates least privilege | Would allow public-access enablement, bucket deletion, etc. |

### DynamoDB

| Action | Granted | Why | Why NOT broader |
|---|---|---|---|
| `dynamodb:PutItem` | Yes | Write alert items on Orange/Red transitions | Only writes new items — does not need Update or Delete |
| `dynamodb:Query` | Yes | Dashboard reads latest alerts by zone_id | Scoped to named table ARN — no other tables accessible |
| `dynamodb:Scan` | Yes | "Last N alerts" dashboard query across all zones | Acceptable given small table size and infrequent calls |
| `dynamodb:UpdateItem` | **No** | App only appends new alerts, never mutates old ones | Prevents overwriting historical audit records |
| `dynamodb:DeleteItem` | **No** | TTL handles expiry automatically | Prevents alert record deletion (audit integrity) |
| `dynamodb:CreateTable`, `dynamodb:DeleteTable` | **No** | Management plane | Table is pre-created; app should not manage schema |
| `dynamodb:*` wildcard | **No** | Would allow table deletion, stream configuration, etc. | — |

### SNS

| Action | Granted | Why | Why NOT broader |
|---|---|---|---|
| `sns:Publish` on named ARN | Yes | Publish Orange/Red alert messages | Scoped to the single topic — cannot publish to any other topic |
| `sns:Subscribe` | **No** | Subscriptions are configured manually pre-demo | Prevents app from adding arbitrary subscribers |
| `sns:CreateTopic`, `sns:DeleteTopic` | **No** | Management plane | Topic is pre-created |
| `sns:SetTopicAttributes` | **No** | No runtime need | Prevents policy/encryption modification |
| `sns:Publish` on `*` | **No** | Would allow publishing to any SNS topic in the account | — |

---

## S3 Bucket Policy (separate from IAM — applied to the bucket itself)

This denies all public access and enforces that only the EC2 instance profile
may read/write objects. Apply this to the bucket in addition to the IAM policy
for defense-in-depth.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyPublicAccess",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::swarmsight-demo-kesha",
        "arn:aws:s3:::swarmsight-demo-kesha/*"
      ],
      "Condition": {
        "Bool": {
          "aws:SecureTransport": "false"
        }
      }
    },
    {
      "Sid": "AllowEC2RoleOnly",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::879268673611:role/swarmsight-ec2-role"
      },
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::swarmsight-demo-kesha",
        "arn:aws:s3:::swarmsight-demo-kesha/*"
      ]
    }
  ]
}
```

---

## Security Hardening Checklist

| Control | Status | How |
|---|---|---|
| No hardcoded credentials | Required | Instance profile only — no env vars or ~/.aws/credentials |
| IMDSv2 enforced | Recommended | Set `HttpTokens=required` on the EC2 instance at launch |
| S3 Block Public Access | Required | All 4 settings enabled on the bucket |
| S3 HTTPS-only | Required | Bucket policy `aws:SecureTransport: false` → Deny |
| S3 SSE-S3 encryption | Required | Default encryption on bucket (AES-256) |
| DynamoDB encryption | Default | AWS-managed key applied automatically |
| Security group: SSH only to your IP | Required | sg inbound rule: port 22, source /32 |
| Security group: app only to your IP | Required | sg inbound rule: port 8000, source /32 |
| No IAM actions in policy | Required | Policy contains zero iam:* actions |
| No EC2 actions in policy | Required | Policy contains zero ec2:* actions |
| CloudTrail | Optional for demo, required for production | Enabled by default in ap-south-1 on account 879268673611 |

---

## Verification Commands (run from inside EC2 after IAM role is attached)

```bash
# Confirm role is active — should return swarmsight-ec2-role ARN
aws sts get-caller-identity

# S3 — list clips prefix
aws s3 ls s3://swarmsight-demo-kesha/clips/

# DynamoDB — confirm table is accessible
aws dynamodb list-tables --region ap-south-1

# SNS — confirm topic is visible
aws sns list-topics --region ap-south-1

# Negative test — this must fail (no ec2:DescribeInstances in policy)
aws ec2 describe-instances --region ap-south-1
# Expected: An error occurred (UnauthorizedOperation)
```

All four positive tests must succeed and the negative test must fail before
running the application. This proves the role is attached and scoped correctly.
