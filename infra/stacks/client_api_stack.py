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

API key access (ADR-0021, ported 2026-11-23 from the showcase repo with identical construct IDs so
the keys it already deployed to dev are kept, not replaced): the contract declares an `api_key`
security scheme and a `security` requirement on every operation except the CORS `OPTIONS`
preflight. This stack creates the API keys and usage plans **without any key value** (API Gateway
generates it; a human copies it into a GitHub secret by hand). A FRONTEND key sits in its own
usage plan in every environment; a CI/EVAL key with its own Client API usage plan exists in `dev`
only, never in `prod`.

Rollout (chicken and egg: the key value only exists after the first deploy): enforcement is
controlled by the CDK context flag `require_api_key`. While it is false the stack still creates
the keys and usage plans but removes the `security` requirements from the deployed contract, so
the API stays open and the dev front end keeps working. After the FRONTEND value is in the
`FRONTEND_API_KEY` secret and the front end sends it, set the flag to true.
Default: not enforcing in `dev` (staged rollout). Every other environment always enforces
(fail closed): the flag cannot relax enforcement outside `dev`.

The CI/EVAL key is consumed cross-stack by `tendril-dev-eval-control` (the showcase's Evaluation
Control API, ADR-0019). This repo keeps that link through `EvalControlPlaceholderStack`, which
references `eval_api_key` so CDK keeps generating the export CloudFormation refuses to drop while
it is imported (`infra/stacks/eval_control_stack.py`).
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

# CORS header values (API Gateway wants literal values wrapped in single quotes). Keep the
# header list in sync with the OPTIONS integration in app/api/openapi.yaml.
CORS_ALLOW_ORIGIN = "'*'"
CORS_ALLOW_HEADERS = "'Content-Type,X-User-Id,x-api-key'"

# Usage-plan defaults (same numbers as the showcase deployment, so nothing changes on deploy).
# The FRONTEND plan is throttling plus a generous daily quota (ADR-0021 decision 1). The owner can
# override every number through the CDK context keys used in `_add_api_keys_and_usage_plans`.
DEFAULT_FRONTEND_RATE = 25
DEFAULT_FRONTEND_BURST = 50
DEFAULT_FRONTEND_DAILY_QUOTA = 50_000
DEFAULT_EVAL_RATE = 5
DEFAULT_EVAL_BURST = 10
DEFAULT_EVAL_DAILY_QUOTA = 2_000


def _truthy(value) -> bool:
    """CDK context values arrive as bool (cdk.json) or str (`--context k=true`); None is false."""
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


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
            # 29s = API Gateway's own integration ceiling. Raised from 10s for bulkCreatePlants
            # (ADR-0032): one transaction per stake number, up to 200 per call.
            timeout=Duration.seconds(29),
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
        # PA-03: getGoalDetail signs a presigned GET url the same way — generate_presigned_url()
        # succeeds regardless of IAM (it only signs), so this gap only surfaces as a real 403 the
        # first time someone actually fetches the URL, exactly the bug grant_put's own comment
        # above already warned about, just for GET instead of PUT.
        media_bucket.grant_read(garden_handler)
        # WS-03: createGoal publishes goal.submitted to the default event bus — AgentCoreStack's
        # rule (matching on source/detail-type, not this Lambda directly) routes it to the
        # orchestrator asynchronously (ADR-0004/ADR-0012).
        garden_handler.add_to_role_policy(
            iam.PolicyStatement(
                actions=["events:PutEvents"],
                resources=[f"arn:aws:events:{self.region}:{self.account}:event-bus/default"],
            )
        )

        # --- API key enforcement flag (staged rollout, see module docstring) ---
        # `dev` enforces only when the flag is explicitly true (default: not enforcing, so the
        # first deploy does not break the front end). Any other environment always enforces:
        # the flag can only relax enforcement in dev, never in prod.
        self.require_api_key = env_name != "dev" or _truthy(
            self.node.try_get_context("require_api_key")
        )

        # --- Load + patch the OpenAPI spec, one Lambda for both operations for now ---
        with open(OPENAPI_PATH, encoding="utf-8") as f:
            spec = yaml.safe_load(f)

        integration_uri = (
            f"arn:aws:apigateway:{self.region}:lambda:path/2015-03-31/functions/"
            f"{garden_handler.function_arn}/invocations"
        )
        # Path items may carry a path-level `parameters` list (WS-01: gardenId is now declared
        # once per path, not per-operation) alongside HTTP-method keys — skip anything that
        # isn't an operation object, since it won't have `.get("x-amazon-apigateway-integration")`.
        for methods in spec.get("paths", {}).values():
            for operation in methods.values():
                if not isinstance(operation, dict):
                    continue
                integration = operation.get("x-amazon-apigateway-integration")
                if integration and integration.get("uri") == LAMBDA_URI_PLACEHOLDER:
                    integration["uri"] = integration_uri
                # Not enforcing yet: drop the `security` requirement so API Gateway does not
                # mark the method apiKeyRequired. The contract file keeps the requirement.
                if not self.require_api_key:
                    operation.pop("security", None)

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

        self._add_cors_gateway_responses()
        self._add_api_keys_and_usage_plans(env_name, prefix)

        CfnOutput(self, "ClientApiUrl", value=self.api.url)
        CfnOutput(self, "GardenHandlerArn", value=garden_handler.function_arn)

    def _add_cors_gateway_responses(self) -> None:
        """Default 4XX/5XX gateway responses carry the CORS headers (ADR-0021 decision 7).

        API Gateway answers a missing key (403) or a throttled call (429) itself, before the
        Lambda runs, so without these headers the browser reports an opaque CORS error instead
        of letting the front end read the status.
        """
        headers = {
            "Access-Control-Allow-Origin": CORS_ALLOW_ORIGIN,
            "Access-Control-Allow-Headers": CORS_ALLOW_HEADERS,
        }
        self.api.add_gateway_response(
            "Default4xx", type=apigateway.ResponseType.DEFAULT_4_XX, response_headers=headers
        )
        self.api.add_gateway_response(
            "Default5xx", type=apigateway.ResponseType.DEFAULT_5_XX, response_headers=headers
        )

    def _ctx_int(self, name: str, default: int) -> int:
        value = self.node.try_get_context(name)
        return default if value is None else int(value)

    def _add_api_keys_and_usage_plans(self, env_name: str, prefix: str) -> None:
        """Keys and usage plans (ADR-0021 decisions 1, 3, 5, 8).

        No `value` is ever passed to `ApiKey`: API Gateway generates it and a human copies it
        into a GitHub secret. Only key IDs appear in outputs. Construct IDs must stay exactly as
        they are: they match the keys already deployed to dev, and renaming one would replace
        the key (a new value) and break the eval-control import.
        """
        stage = self.api.deployment_stage

        self.frontend_api_key = apigateway.ApiKey(
            self,
            "FrontendApiKey",
            api_key_name=f"{prefix}-frontend",
            description="FRONTEND key (public by nature: identification and throttling only)",
        )
        frontend_plan = apigateway.UsagePlan(
            self,
            "FrontendUsagePlan",
            name=f"{prefix}-frontend",
            description="Client API usage plan for the FRONTEND key",
            throttle=apigateway.ThrottleSettings(
                rate_limit=self._ctx_int("frontend_throttle_rate", DEFAULT_FRONTEND_RATE),
                burst_limit=self._ctx_int("frontend_throttle_burst", DEFAULT_FRONTEND_BURST),
            ),
            quota=apigateway.QuotaSettings(
                limit=self._ctx_int("frontend_daily_quota", DEFAULT_FRONTEND_DAILY_QUOTA),
                period=apigateway.Period.DAY,
            ),
            api_stages=[apigateway.UsagePlanPerApiStage(api=self.api, stage=stage)],
        )
        frontend_plan.add_api_key(self.frontend_api_key)
        CfnOutput(self, "FrontendApiKeyId", value=self.frontend_api_key.key_id)

        # CI/EVAL key: dev only. prod never gets an eval key or an eval usage plan.
        self.eval_api_key = None
        if env_name == "dev":
            self.eval_api_key = apigateway.ApiKey(
                self,
                "EvalApiKey",
                api_key_name=f"{prefix}-ci-eval",
                description="CI/EVAL key (secret): evaluation and acceptance callers",
            )
            eval_plan = apigateway.UsagePlan(
                self,
                "EvalClientApiUsagePlan",
                name=f"{prefix}-ci-eval-client-api",
                description="Client API usage plan for the CI/EVAL key",
                throttle=apigateway.ThrottleSettings(
                    rate_limit=self._ctx_int("eval_throttle_rate", DEFAULT_EVAL_RATE),
                    burst_limit=self._ctx_int("eval_throttle_burst", DEFAULT_EVAL_BURST),
                ),
                quota=apigateway.QuotaSettings(
                    limit=self._ctx_int("eval_daily_quota", DEFAULT_EVAL_DAILY_QUOTA),
                    period=apigateway.Period.DAY,
                ),
                api_stages=[apigateway.UsagePlanPerApiStage(api=self.api, stage=stage)],
            )
            eval_plan.add_api_key(self.eval_api_key)
            CfnOutput(self, "EvalApiKeyId", value=self.eval_api_key.key_id)
