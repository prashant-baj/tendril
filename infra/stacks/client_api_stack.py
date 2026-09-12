"""Client API stack: OpenAPI contract-first API Gateway + its Lambda handler(s).

Per ADR-0011, `app/api/openapi.yaml` is the actual deploy artifact — this stack loads it,
patches each operation's `x-amazon-apigateway-integration.uri` with the real Lambda invoke
ARN, and constructs `apigateway.SpecRestApi` from the patched document. Routes are never
defined a second time in CDK; the spec is the only source of truth for what API Gateway
looks like.

The DynamoDB table is resolved by its **stable, predictable name** (`tendril-{env}-app`,
`FoundationStack`), not a CDK cross-stack reference — same "resolve by stable name, no CFN
cross-stack import" posture already established for prompts/guardrails (ADR-0008's deployment
topology note), so this stack deploys independently of FoundationStack's exact CFN exports.

Per ADR-0014, every Lambda here is a **container image** (ARM64, `app/api/Dockerfile`), built
via `DockerImageCode.from_image_asset`. Pass context `garden_handler_image_repo` (+ optional
`garden_handler_image_tag`) to build from a pre-existing ECR repository instead — used by unit
tests (and CI, if it ever pre-builds images) to synth without a local Docker build, mirroring
`AgentCoreStack`'s `agent_image_uri` escape hatch.
"""

from pathlib import Path

import yaml
from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
    aws_apigateway as apigateway,
    aws_dynamodb as ddb,
    aws_ecr as ecr,
    aws_ecr_assets as ecr_assets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
)
from constructs import Construct

APP_API_DIR = Path(__file__).resolve().parents[2] / "app" / "api"
OPENAPI_PATH = APP_API_DIR / "openapi.yaml"
LAMBDA_URI_PLACEHOLDER = "__LAMBDA_INTEGRATION_URI__"


class ClientApiStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, env_name: str, **kwargs) -> None:
        super().__init__(scope, cid, **kwargs)
        prefix = f"tendril-{env_name}"

        # Resolve AppTable/MediaBucket by stable name (no CloudFormation cross-stack import).
        app_table = ddb.Table.from_table_name(self, "AppTable", f"{prefix}-app")
        media_bucket = s3.Bucket.from_bucket_name(self, "MediaBucket", f"{prefix}-media")

        # --- Garden handler (OB-01: createGarden, getGarden; OB-02: createMediaUpload, createPlant) ---
        image_repo = self.node.try_get_context("garden_handler_image_repo")
        if image_repo:
            repo = ecr.Repository.from_repository_name(self, "GardenHandlerRepo", image_repo)
            image_tag = self.node.try_get_context("garden_handler_image_tag") or "latest"
            code = lambda_.DockerImageCode.from_ecr(repo, tag=image_tag)
        else:
            code = lambda_.DockerImageCode.from_image_asset(
                str(APP_API_DIR), platform=ecr_assets.Platform.LINUX_ARM64
            )

        garden_handler = lambda_.DockerImageFunction(
            self,
            "GardenHandler",
            function_name=f"{prefix}-garden-handler",
            code=code,
            architecture=lambda_.Architecture.ARM_64,
            timeout=Duration.seconds(10),
            environment={
                "APP_TABLE_NAME": app_table.table_name,
                "MEDIA_BUCKET_NAME": media_bucket.bucket_name,
            },
        )
        app_table.grant_read_write_data(garden_handler)
        # grant_read_write_data doesn't include Transact*; createGarden/createPlant need it
        # explicitly (data-architecture.md §2: Garden's canonical + ownership-index records,
        # and a Plant + its linked Media record, are each written together in one
        # TransactWriteItems call).
        garden_handler.add_to_role_policy(
            iam.PolicyStatement(
                actions=["dynamodb:TransactWriteItems"],
                resources=[app_table.table_arn],
            )
        )
        # OB-02: createMediaUpload signs a presigned PUT URL — the signing identity (this
        # Lambda's role) must actually be authorized for the action, or the URL 403s when used.
        media_bucket.grant_put(garden_handler)

        # --- Load + patch the OpenAPI spec, one Lambda for both operations for now ---
        with open(OPENAPI_PATH, encoding="utf-8") as f:
            spec = yaml.safe_load(f)

        integration_uri = (
            f"arn:aws:apigateway:{self.region}:lambda:path/2015-03-31/functions/"
            f"{garden_handler.function_arn}/invocations"
        )
        for methods in spec.get("paths", {}).values():
            for operation in methods.values():
                integration = operation.get("x-amazon-apigateway-integration")
                if integration and integration.get("uri") == LAMBDA_URI_PLACEHOLDER:
                    integration["uri"] = integration_uri

        self.api = apigateway.SpecRestApi(
            self,
            "ClientApi",
            rest_api_name=f"{prefix}-client-api",
            api_definition=apigateway.ApiDefinition.from_inline(spec),
        )
        # SpecRestApi doesn't auto-wire Lambda permissions the way LambdaRestApi does.
        garden_handler.add_permission(
            "ApiGatewayInvoke",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_arn=self.api.arn_for_execute_api(),
        )

        CfnOutput(self, "ClientApiUrl", value=self.api.url)
        CfnOutput(self, "GardenHandlerArn", value=garden_handler.function_arn)
