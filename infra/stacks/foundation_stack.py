"""Foundation stack: media storage + structured state tables.

All resources are env-prefixed. Prod retains data; dev is destroyable.
See docs/architecture/ADRs/0002-context-management-and-durable-state.md
and 0004-backend-api-serverless-storage.md.
"""

from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_dynamodb as ddb,
    aws_s3 as s3,
)
from constructs import Construct


class FoundationStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, env_name: str, **kwargs) -> None:
        super().__init__(scope, cid, **kwargs)
        prefix = f"tendril-{env_name}"
        retain = env_name == "prod"
        removal = RemovalPolicy.RETAIN if retain else RemovalPolicy.DESTROY

        # Media: photos / documents (video-ready). Uploaded via presigned URLs.
        self.media_bucket = s3.Bucket(
            self,
            "MediaBucket",
            bucket_name=f"{prefix}-media",
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=removal,
            auto_delete_objects=not retain,
            # OB-02: the frontend PUTs directly to a presigned URL (never through the Lambda),
            # a genuine cross-origin request from the frontend's own origin (ADR-0010) — the
            # bucket needs to allow it. `*` matches this Client API's own dev-stage CORS
            # posture (no auth yet, ADR-0004's seam still open); narrow to the real origin(s)
            # once one is fixed (e.g. a custom domain/CloudFront, ADR-0010's follow-up).
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.PUT],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                )
            ],
        )

        # Structured domain state + capture-first event log (single-table design).
        self.app_table = ddb.Table(
            self,
            "AppTable",
            table_name=f"{prefix}-app",
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=retain
            ),
            removal_policy=removal,
        )

        # WebSocket connection registry (ADR-0004).
        self.connections_table = ddb.Table(
            self,
            "ConnectionsTable",
            table_name=f"{prefix}-ws-connections",
            partition_key=ddb.Attribute(name="connectionId", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=removal,
        )

        CfnOutput(self, "MediaBucketName", value=self.media_bucket.bucket_name)
        CfnOutput(self, "AppTableName", value=self.app_table.table_name)
