/**
 * SwarmSight Infrastructure — Configuration
 *
 * Single source of truth for all resource names and parameters.
 * Edit this file if you need to change the bucket name suffix or region.
 */
export const CONFIG = {
  /** AWS account — resolved from CDK environment, kept here for reference */
  ACCOUNT: '879268673611',

  /** Primary deployment region */
  REGION: 'ap-south-1',

  // ── Resource Names ──────────────────────────────────────────────────────

  /** S3 bucket name — must be globally unique */
  BUCKET_NAME: 'swarmsight-demo-kesha',

  /** DynamoDB table name — matches deployment plan exactly */
  DYNAMO_TABLE: 'swarmsight_alerts',

  /** SNS topic name */
  SNS_TOPIC_NAME: 'swarmsight-alerts',

  /** SNS display name shown in SMS/email sender field */
  SNS_DISPLAY_NAME: 'SwarmSight Alert',

  /** EC2 instance Name tag */
  EC2_NAME: 'swarmsight-demo',

  /** IAM role and instance profile names */
  IAM_ROLE_NAME: 'swarmsight-ec2-role',
  INSTANCE_PROFILE_NAME: 'swarmsight-ec2-profile',

  /** Security group name */
  SG_NAME: 'swarmsight-sg',

  // ── EC2 Parameters ──────────────────────────────────────────────────────

  /** Instance type — t3.large gives 2 vCPU / 8 GB for CPU inference at 2-5 fps */
  INSTANCE_TYPE: 't3.large',

  /** Root EBS volume size in GB — enough for model weights + OS + code */
  ROOT_VOLUME_GB: 30,

  /**
   * Key pair name for SSH access.
   * Set via CDK context: --context keyPairName=YOUR_KEY
   * If not provided, the instance launches without a key pair
   * (you can still connect via EC2 Instance Connect or SSM).
   */
  KEY_PAIR_CONTEXT_VAR: 'keyPairName',

  // ── Lifecycle / TTL ─────────────────────────────────────────────────────

  /** Days after which snapshots/ prefix objects expire from S3 */
  SNAPSHOT_EXPIRY_DAYS: 7,

  /** DynamoDB TTL attribute name — matches the item schema in architecture.md */
  DYNAMO_TTL_ATTRIBUTE: 'ttl',
} as const;
