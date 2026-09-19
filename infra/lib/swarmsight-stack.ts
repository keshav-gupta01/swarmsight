import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Construct } from 'constructs';
import { CONFIG } from './config';

/**
 * SwarmSightStack
 *
 * Provisions the minimal five-resource AWS architecture described in
 * docs/architecture.md for the 10-hour demo build.
 *
 * Resources created (in dependency order):
 *   1. S3 bucket          — private, SSE-S3, snapshot lifecycle
 *   2. DynamoDB table     — swarmsight_alerts, on-demand, TTL enabled
 *   3. SNS topic          — Standard, Orange/Red alert delivery
 *   4. IAM role           — least-privilege policy for the three resources above
 *   5. Security group     — SSH + app port (restrict to your IP before deploy)
 *   6. EC2 instance       — t3.large, Ubuntu 22.04, IMDSv2, UserData bootstrap
 */
export class SwarmSightStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ════════════════════════════════════════════════════════════════════
    // 1. S3 BUCKET
    // ════════════════════════════════════════════════════════════════════

    const demoBucket = new s3.Bucket(this, 'DemoBucket', {
      bucketName: CONFIG.BUCKET_NAME,
      // Keep private — all four Block Public Access settings on
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      // Enforce HTTPS-only access
      enforceSSL: true,
      // SSE-S3 (AES-256) — cheapest option, sufficient for a demo
      encryption: s3.BucketEncryption.S3_MANAGED,
      // Versioning off — we don't need version history for demo clips
      versioned: false,
      /**
       * RETAIN on removal: prevents accidental clip deletion if the stack
       * is torn down while demo data is still needed by judges.
       * Change to DESTROY only when fully cleaning up.
       */
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      autoDeleteObjects: false,
      // Expire snapshots/ prefix automatically — privacy hygiene
      lifecycleRules: [
        {
          id: 'expire-snapshots',
          prefix: 'snapshots/',
          expiration: cdk.Duration.days(CONFIG.SNAPSHOT_EXPIRY_DAYS),
          enabled: true,
        },
      ],
    });

    // ════════════════════════════════════════════════════════════════════
    // 2. DYNAMODB TABLE — swarmsight_alerts
    // ════════════════════════════════════════════════════════════════════

    const alertsTable = new dynamodb.Table(this, 'AlertsTable', {
      tableName: CONFIG.DYNAMO_TABLE,
      // Composite primary key per architecture.md:
      //   PK: zone_id  (String)  e.g. "grid_3_2"
      //   SK: timestamp (Number) Unix epoch in milliseconds
      partitionKey: {
        name: 'zone_id',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'timestamp',
        type: dynamodb.AttributeType.NUMBER,
      },
      // On-demand — no capacity planning needed for a demo
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      // TTL — items expire automatically after 7 days (see architecture.md)
      timeToLiveAttribute: CONFIG.DYNAMO_TTL_ATTRIBUTE,
      // Encryption with AWS-managed key (default — no extra cost)
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      // RETAIN for same reason as S3 — keep alert history for judges
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ════════════════════════════════════════════════════════════════════
    // 3. SNS TOPIC — swarmsight-alerts
    // ════════════════════════════════════════════════════════════════════

    const alertsTopic = new sns.Topic(this, 'AlertsTopic', {
      topicName: CONFIG.SNS_TOPIC_NAME,
      displayName: CONFIG.SNS_DISPLAY_NAME,
      // Standard (not FIFO) — ordering not required for safety alerts
      fifo: false,
    });

    // Subscriptions (SMS + email) are NOT created by CDK here.
    // They require confirmation from the subscriber, so add them manually
    // via the AWS Console or CLI after deployment (see deployment-plan.md §0.6).
    // Adding them in CDK would leave them permanently in PendingConfirmation
    // unless the subscriber acts immediately during `cdk deploy`.

    // ════════════════════════════════════════════════════════════════════
    // 4. IAM ROLE + INSTANCE PROFILE
    //    Least-privilege: only the three named resources above, no wildcards
    // ════════════════════════════════════════════════════════════════════

    const ec2Role = new iam.Role(this, 'Ec2Role', {
      roleName: CONFIG.IAM_ROLE_NAME,
      // Trust policy — only EC2 instances can assume this role
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      description: 'SwarmSight demo EC2 instance role - scoped to S3/DDB/SNS only',
    });

    // ── S3 permissions ────────────────────────────────────────────────

    // ListBucket on the bucket (with prefix condition)
    ec2Role.addToPolicy(new iam.PolicyStatement({
      sid: 'S3ListBucket',
      effect: iam.Effect.ALLOW,
      actions: ['s3:ListBucket'],
      resources: [demoBucket.bucketArn],
      conditions: {
        StringLike: {
          's3:prefix': ['clips/*', 'snapshots/*'],
        },
      },
    }));

    // GetObject — clips/ only (app reads video frames from here)
    ec2Role.addToPolicy(new iam.PolicyStatement({
      sid: 'S3ReadClips',
      effect: iam.Effect.ALLOW,
      actions: ['s3:GetObject'],
      resources: [demoBucket.arnForObjects('clips/*')],
    }));

    // PutObject — snapshots/ only (optional: write alerting frame grids)
    ec2Role.addToPolicy(new iam.PolicyStatement({
      sid: 'S3WriteSnapshots',
      effect: iam.Effect.ALLOW,
      actions: ['s3:PutObject'],
      resources: [demoBucket.arnForObjects('snapshots/*')],
    }));

    // ── DynamoDB permissions ──────────────────────────────────────────

    // Write alerts + query dashboard — no Delete, no UpdateItem
    ec2Role.addToPolicy(new iam.PolicyStatement({
      sid: 'DynamoDBAlerts',
      effect: iam.Effect.ALLOW,
      actions: [
        'dynamodb:PutItem',   // write alert on Orange/Red transition
        'dynamodb:Query',     // dashboard: alerts by zone_id
        'dynamodb:Scan',      // dashboard: all alerts in last N minutes
      ],
      resources: [alertsTable.tableArn],
    }));

    // ── SNS permissions ───────────────────────────────────────────────

    // Publish to the single named topic — no Subscribe, no CreateTopic
    ec2Role.addToPolicy(new iam.PolicyStatement({
      sid: 'SNSPublishAlerts',
      effect: iam.Effect.ALLOW,
      actions: ['sns:Publish'],
      resources: [alertsTopic.topicArn],
    }));

    // ════════════════════════════════════════════════════════════════════
    // 5. SECURITY GROUP
    // ════════════════════════════════════════════════════════════════════

    // Use the default VPC — no custom VPC needed for a single-instance demo
    const vpc = ec2.Vpc.fromLookup(this, 'DefaultVpc', { isDefault: true });

    const sg = new ec2.SecurityGroup(this, 'AppSecurityGroup', {
      securityGroupName: CONFIG.SG_NAME,
      vpc,
      description: 'SwarmSight demo - SSH + app port',
      // Do NOT allow all outbound traffic to be restricted — instance needs
      // to reach AWS API endpoints (S3, DDB, SNS) and package repositories
      allowAllOutbound: true,
    });

    /**
     * SECURITY NOTE: Inbound rules below use 0.0.0.0/0 as a PLACEHOLDER.
     * Before deploying, restrict both rules to your IP /32.
     *
     * Option A: Edit this file → replace Peer.anyIpv4() with Peer.ipv4('x.x.x.x/32')
     * Option B: Modify the security group in the console after deploy
     *
     * The CfnOutput below reminds you of this at synth time.
     */
    sg.addIngressRule(
      ec2.Peer.ipv4('47.15.119.56/32'),
      ec2.Port.tcp(22),
      'SSH - developer local IP',
    );

    // EC2 Instance Connect service IP block for ap-south-1 (AWS Console browser terminal)
    sg.addIngressRule(
      ec2.Peer.ipv4('13.233.177.0/29'),
      ec2.Port.tcp(22),
      'EC2 Instance Connect service CIDR (ap-south-1)',
    );

    sg.addIngressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(8000),
      'FastAPI/WebSocket - Web console demo access',
    );

    // ════════════════════════════════════════════════════════════════════
    // 6. EC2 INSTANCE
    // ════════════════════════════════════════════════════════════════════

    // Ubuntu 22.04 LTS — latest patch, Canonical owned
    const ubuntuAmi = ec2.MachineImage.fromSsmParameter(
      '/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id',
      { os: ec2.OperatingSystemType.LINUX },
    );

    // UserData: install Python stack and project dependencies on first boot
    const userData = ec2.UserData.forLinux();
    userData.addCommands(
      'set -euxo pipefail',
      '',
      '# System packages',
      'apt-get update -y',
      'DEBIAN_FRONTEND=noninteractive apt-get install -y \\',
      '  python3.11 python3.11-venv python3-pip ffmpeg git screen',
      '',
      '# Create app directory and virtualenv',
      'mkdir -p /opt/swarmsight/app/static',
      'python3.11 -m venv /opt/swarmsight/venv',
      'source /opt/swarmsight/venv/bin/activate',
      '',
      '# Core Python dependencies',
      'pip install --upgrade pip',
      'pip install \\',
      '  fastapi "uvicorn[standard]" \\',
      '  opencv-python-headless \\',
      '  numpy boto3',
      '',
      '# Verify AWS role is working (will show swarmsight-ec2-role)',
      'aws sts get-caller-identity --region ap-south-1 >> /var/log/swarmsight-setup.log 2>&1 || true',
      '',
      'echo "SwarmSight bootstrap complete" >> /var/log/swarmsight-setup.log',
    );

    // Resolve optional key pair from CDK context (--context keyPairName=...)
    const keyPairName = this.node.tryGetContext(CONFIG.KEY_PAIR_CONTEXT_VAR) as string | undefined;

    const instance = new ec2.Instance(this, 'AppInstance', {
      instanceName: CONFIG.EC2_NAME,
      vpc,
      instanceType: new ec2.InstanceType(CONFIG.INSTANCE_TYPE),
      machineImage: ubuntuAmi,
      securityGroup: sg,
      role: ec2Role,
      userData,
      // 30 GB gp3 root — model weights + OS + code comfortably fit
      blockDevices: [
        {
          deviceName: '/dev/sda1',
          volume: ec2.BlockDeviceVolume.ebs(CONFIG.ROOT_VOLUME_GB, {
            volumeType: ec2.EbsDeviceVolumeType.GP3,
            encrypted: true,        // encrypt at rest with AWS-managed key
            deleteOnTermination: true,
          }),
        },
      ],
      // IMDSv2 required — prevents SSRF attacks from reading instance metadata
      requireImdsv2: true,
      // Place in first AZ of the region
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      ...(keyPairName ? { keyName: keyPairName } : {}),
    });

    // ════════════════════════════════════════════════════════════════════
    // OUTPUTS — displayed after deploy, useful for connecting/debugging
    // ════════════════════════════════════════════════════════════════════

    new cdk.CfnOutput(this, 'BucketName', {
      value: demoBucket.bucketName,
      description: 'S3 bucket for demo clips and snapshots',
      exportName: 'SwarmSight-BucketName',
    });

    new cdk.CfnOutput(this, 'DynamoTableName', {
      value: alertsTable.tableName,
      description: 'DynamoDB table for alert records',
      exportName: 'SwarmSight-DynamoTable',
    });

    new cdk.CfnOutput(this, 'SnsTopicArn', {
      value: alertsTopic.topicArn,
      description: 'SNS topic ARN — subscribe your phone/email here',
      exportName: 'SwarmSight-SnsTopicArn',
    });

    new cdk.CfnOutput(this, 'Ec2InstanceId', {
      value: instance.instanceId,
      description: 'EC2 instance ID',
      exportName: 'SwarmSight-InstanceId',
    });

    new cdk.CfnOutput(this, 'Ec2PublicIp', {
      value: instance.instancePublicIp,
      description: 'EC2 public IP — open http://<IP>:8000 in browser',
      exportName: 'SwarmSight-PublicIp',
    });

    new cdk.CfnOutput(this, 'IamRoleArn', {
      value: ec2Role.roleArn,
      description: 'IAM role attached to EC2 as instance profile',
      exportName: 'SwarmSight-IamRoleArn',
    });

    new cdk.CfnOutput(this, 'SecurityWarning', {
      value: 'BEFORE DEPLOYING: restrict SG inbound rules to your IP /32 — see lib/swarmsight-stack.ts SECURITY NOTE',
      description: 'Security reminder',
    });
  }
}
