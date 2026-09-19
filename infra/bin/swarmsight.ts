#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { SwarmSightStack } from '../lib/swarmsight-stack';
import { CONFIG } from '../lib/config';

const app = new cdk.App();

new SwarmSightStack(app, 'SwarmSightStack', {
  /**
   * Explicitly pin account + region so CDK never infers them from
   * environment defaults. Uses the SSO profile in ~/.aws/config.
   *
   * To synthesize: set AWS_PROFILE or pass --profile to cdk
   * Example: npx cdk synth --profile SwarmSightAntigravityReadOnly-879268673611
   */
  env: {
    account: CONFIG.ACCOUNT,
    region: CONFIG.REGION,
  },
  description: 'SwarmSight — minimal demo infrastructure (S3 + DynamoDB + SNS + EC2 + IAM)',
  tags: {
    Project: 'SwarmSight',
    Environment: 'demo',
    ManagedBy: 'CDK',
  },
});
