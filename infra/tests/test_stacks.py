"""CDK assertion tests for the Tendril stacks (no AWS deploy).

Synthesizes each stack to a CloudFormation template and asserts the resources and wiring
we depend on: guardrail policies, the file-sourced prompt, and the agent runtime's env
vars + IAM. Runs offline; skips if aws-cdk-lib isn't installed.
"""

import pytest

aws_cdk = pytest.importorskip("aws_cdk")
from aws_cdk import App, Environment  # noqa: E402
from aws_cdk.assertions import Match, Template  # noqa: E402
from stacks.agentcore_stack import AgentCoreStack  # noqa: E402
from stacks.client_api_stack import ClientApiStack  # noqa: E402
from stacks.foundation_stack import FoundationStack  # noqa: E402
from stacks.frontend_stack import FrontendStack  # noqa: E402
from stacks.guardrails_stack import GuardrailsStack  # noqa: E402
from stacks.pipeline_stack import PipelineStack  # noqa: E402
from stacks.prompts_stack import PromptsStack  # noqa: E402

ENV = Environment(account="123456789012", region="ap-south-1")
IMAGE = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/tendril:latest"


def _app() -> App:
    # agent_image_uri / garden_handler_image_repo / orchestrator_image_repo / *_tool_image_repo
    # avoid a Docker build during synth (ADR-0005, ADR-0014).
    return App(
        context={
            "agent_image_uri": IMAGE,
            "model_id": "global.amazon.nova-2-lite-v1:0",
            "garden_handler_image_repo": "tendril-dev-garden-handler",
            "orchestrator_image_repo": "tendril-dev-orchestrator",
            "weather_tool_image_repo": "tendril-dev-tool-weather",
        }
    )


# --- guardrails --------------------------------------------------------------
# Static policy-file checks (description/topic/messaging length limits Bedrock enforces
# that cdk synth doesn't catch) live in tests/test_guardrail_policy.py, generalized over
# every guardrails/*.json file. The tests below are CDK-synth assertions instead.


def test_guardrail_resource_and_policies():
    app = _app()
    tpl = Template.from_stack(GuardrailsStack(app, "gr", env_name="dev", env=ENV))
    # One per guardrails/*.json (AF-01: vision, WS-04's vision specialist; hello was
    # decommissioned once vision took over as the orchestrator's proof specialist).
    tpl.resource_count_is("AWS::Bedrock::Guardrail", 1)
    tpl.resource_count_is("AWS::Bedrock::GuardrailVersion", 1)
    tpl.has_resource_properties(
        "AWS::Bedrock::Guardrail",
        {
            "Name": "tendril-dev-vision-guardrail",
            "ContentPolicyConfig": Match.object_like(
                {"FiltersConfig": Match.array_with([Match.object_like({"Type": "PROMPT_ATTACK"})])}
            ),
            "TopicPolicyConfig": Match.object_like(
                {
                    "TopicsConfig": Match.array_with(
                        [Match.object_like({"Name": "UnsafeChemicalUse", "Type": "DENY"})]
                    )
                }
            ),
            "SensitiveInformationPolicyConfig": Match.object_like(
                {
                    "PiiEntitiesConfig": Match.array_with(
                        [Match.object_like({"Type": "EMAIL", "Action": "ANONYMIZE"})]
                    )
                }
            ),
        },
    )


# --- prompts (sourced from file) --------------------------------------------


def test_prompt_uses_file_text():
    app = _app()
    tpl = Template.from_stack(PromptsStack(app, "pr", env_name="dev", env=ENV))
    # One per PROMPT_CATALOG entry (vision, WS-04's vision specialist; hello was decommissioned
    # once vision took over as the orchestrator's proof specialist).
    tpl.resource_count_is("AWS::Bedrock::Prompt", 1)
    tpl.has_resource_properties(
        "AWS::Bedrock::Prompt",
        Match.object_like(
            {
                "Name": "tendril-dev-vision-system",
                "Variants": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "TemplateConfiguration": {
                                    "Text": {"Text": Match.string_like_regexp("plant")}
                                }
                            }
                        )
                    ]
                ),
            }
        ),
    )


# --- agent runtime env + IAM -------------------------------------------------


def test_agentcore_env_and_iam():
    app = _app()
    tpl = Template.from_stack(AgentCoreStack(app, "ac", env_name="dev", env=ENV))

    # Runtime receives the three stable-name / config env vars.
    tpl.has_resource_properties(
        "AWS::BedrockAgentCore::Runtime",
        Match.object_like(
            {
                "EnvironmentVariables": Match.object_like(
                    {
                        "PROMPT_NAME": "tendril-dev-vision-system",
                        "GUARDRAIL_NAME": "tendril-dev-vision-guardrail",
                        "MODEL_ID": "qwen.qwen3-vl-235b-a22b",
                    }
                )
            }
        ),
    )

    # Exec role can resolve + apply prompts and guardrails.
    actions = set()
    for res in tpl.find_resources("AWS::IAM::Policy").values():
        for stmt in res["Properties"]["PolicyDocument"]["Statement"]:
            act = stmt["Action"]
            actions.update(act if isinstance(act, list) else [act])
    for needed in [
        "bedrock:ListPrompts",
        "bedrock:GetPrompt",
        "bedrock:ListGuardrails",
        "bedrock:GetGuardrail",
        "bedrock:ApplyGuardrail",
        "bedrock:InvokeModel",
    ]:
        assert needed in actions, f"missing IAM action: {needed}"


# --- registry-driven runtimes + orchestrator (WS-02, ADR-0012) ---------------


def test_one_runtime_provisioned_per_registry_file():
    import json
    from pathlib import Path

    registry_dir = Path(__file__).resolve().parents[2] / "agents" / "registry"
    expected_count = len(list(registry_dir.glob("*.json")))
    assert expected_count >= 1  # sanity: vision.json must exist

    app = _app()
    tpl = Template.from_stack(AgentCoreStack(app, "ac2", env_name="dev", env=ENV))
    tpl.resource_count_is("AWS::BedrockAgentCore::Runtime", expected_count)

    # Adding a fixture file changes the count without touching stack code.
    fixture = registry_dir / "_test_fixture_agent.json"
    fixture.write_text(
        json.dumps(
            {
                "name": "fixtureagent",
                "template": "hello_agent",
                "model_id": "",
                "prompt_name": "vision-system",
                "guardrail_name": "vision-guardrail",
                "description": "test fixture",
                "tools": [],
            }
        ),
        encoding="utf-8",
    )
    try:
        app2 = _app()
        tpl2 = Template.from_stack(AgentCoreStack(app2, "ac3", env_name="dev", env=ENV))
        tpl2.resource_count_is("AWS::BedrockAgentCore::Runtime", expected_count + 1)
    finally:
        fixture.unlink()


# --- Tool APIs: one Lambda + Function URL per app/tools/*/ folder (AF-03) ----


def test_tool_lambda_and_function_url_provisioned():
    app = _app()
    tpl = Template.from_stack(AgentCoreStack(app, "ac6", env_name="dev", env=ENV))

    tpl.has_resource_properties(
        "AWS::Lambda::Function",
        Match.object_like({"FunctionName": "tendril-dev-tool-weather", "PackageType": "Image"}),
    )
    # AWS_IAM (not NONE): only specialists granted lambda:InvokeFunctionUrl can call it.
    tpl.has_resource_properties("AWS::Lambda::Url", Match.object_like({"AuthType": "AWS_IAM"}))


def test_specialist_receives_tools_env_vars_for_its_own_declared_tools():
    app = _app()
    tpl = Template.from_stack(AgentCoreStack(app, "ac7", env_name="dev", env=ENV))

    # vision.json declares tools: ["weather"] — its runtime gets both env vars.
    tpl.has_resource_properties(
        "AWS::BedrockAgentCore::Runtime",
        Match.object_like(
            {
                "EnvironmentVariables": Match.object_like(
                    {
                        "TOOLS": Match.string_like_regexp(r"weather"),
                        "TOOL_ENDPOINTS": Match.any_value(),
                    }
                )
            }
        ),
    )


def test_tool_iam_scoped_to_the_specialist_that_declares_it():
    import json
    from pathlib import Path

    # A fixture specialist that does NOT declare "weather" — proves the grant is scoped per
    # specialist, not handed to every runtime via a shared/wildcard policy (AF-01's deferred AC).
    registry_dir = Path(__file__).resolve().parents[2] / "agents" / "registry"
    fixture = registry_dir / "_test_fixture_no_tools_agent.json"
    fixture.write_text(
        json.dumps(
            {
                "name": "fixturenotools",
                "template": "hello_agent",
                "model_id": "",
                "prompt_name": "vision-system",
                "guardrail_name": "vision-guardrail",
                "description": "test fixture declaring no tools",
                "tools": [],
            }
        ),
        encoding="utf-8",
    )
    try:
        app = _app()
        tpl = Template.from_stack(AgentCoreStack(app, "ac8", env_name="dev", env=ENV))

        granted_on = []
        for logical_id, res in tpl.find_resources("AWS::IAM::Policy").items():
            for stmt in res["Properties"]["PolicyDocument"]["Statement"]:
                act = stmt["Action"]
                actions = act if isinstance(act, list) else [act]
                if "lambda:InvokeFunctionUrl" in actions:
                    assert stmt["Resource"] != "*"
                    granted_on.append(logical_id)

        assert any("Vision" in lid for lid in granted_on), (
            "vision (declares weather) should have lambda:InvokeFunctionUrl"
        )
        assert not any("Fixturenotools" in lid for lid in granted_on), (
            "a specialist that doesn't declare the tool must not get the grant"
        )
    finally:
        fixture.unlink()


def test_orchestrator_invoke_agent_runtime_is_scoped_not_wildcard():
    app = _app()
    tpl = Template.from_stack(AgentCoreStack(app, "ac4", env_name="dev", env=ENV))

    found = False
    for res in tpl.find_resources("AWS::IAM::Policy").values():
        for stmt in res["Properties"]["PolicyDocument"]["Statement"]:
            act = stmt["Action"]
            actions = act if isinstance(act, list) else [act]
            if "bedrock-agentcore:InvokeAgentRuntime" in actions:
                found = True
                assert stmt["Resource"] != "*"
    assert found, "no policy statement grants bedrock-agentcore:InvokeAgentRuntime"


def test_goal_submitted_eventbridge_rule_targets_orchestrator():
    app = _app()
    tpl = Template.from_stack(AgentCoreStack(app, "ac5", env_name="dev", env=ENV))

    tpl.has_resource_properties(
        "AWS::Events::Rule",
        Match.object_like(
            {
                "EventPattern": {
                    "source": ["tendril.client-api"],
                    "detail-type": ["goal.submitted"],
                }
            }
        ),
    )
    tpl.has_resource_properties(
        "AWS::Lambda::Function",
        Match.object_like(
            {
                "FunctionName": "tendril-dev-orchestrator",
                "PackageType": "Image",
                "Environment": Match.object_like(
                    {
                        "Variables": Match.object_like(
                            {
                                "APP_TABLE_NAME": "tendril-dev-app",
                                "MEDIA_BUCKET_NAME": "tendril-dev-media",
                            }
                        )
                    }
                ),
            }
        ),
    )

    # WS-04 (vision specialist): the orchestrator is ADR-0013's trusted-tier exception — it
    # needs s3:GetObject to generate a presigned GET URL for a goal's attached photo.
    actions = set()
    for res in tpl.find_resources("AWS::IAM::Policy").values():
        for stmt in res["Properties"]["PolicyDocument"]["Statement"]:
            act = stmt["Action"]
            actions.update(act if isinstance(act, list) else [act])
    assert "s3:GetObject*" in actions


# --- pipeline (GitHub OIDC deploy role) --------------------------------------


def test_deploy_role_trust_policy_tolerates_github_immutable_id_sub_claim():
    # Regression test: GitHub's OIDC `sub` claim embeds immutable numeric owner/repo
    # IDs (`repo:org@123/repo@456:...`), not just `repo:org/repo:...` — confirmed via
    # CloudTrail against an actual AssumeRoleWithWebIdentity AccessDenied. A trust
    # policy without the `@*` wildcards silently never matches.
    app = App(context={"github_org": "prashant-baj", "github_repo": "tendril"})
    tpl = Template.from_stack(PipelineStack(app, "pl", env_name="dev", env=ENV))
    tpl.has_resource_properties(
        "AWS::IAM::Role",
        Match.object_like(
            {
                "RoleName": "tendril-dev-gh-deploy",
                "AssumeRolePolicyDocument": Match.object_like(
                    {
                        "Statement": Match.array_with(
                            [
                                Match.object_like(
                                    {
                                        "Action": "sts:AssumeRoleWithWebIdentity",
                                        "Condition": Match.object_like(
                                            {
                                                "StringLike": {
                                                    "token.actions.githubusercontent.com:sub": "repo:prashant-baj@*/tendril@*:*"
                                                }
                                            }
                                        ),
                                    }
                                )
                            ]
                        )
                    }
                ),
            }
        ),
    )


# --- client API (OpenAPI contract-first, OB-01/OB-02) ------------------------


def test_client_api_stack_synthesizes_garden_operations():
    app = _app()
    tpl = Template.from_stack(ClientApiStack(app, "capi", env_name="dev", env=ENV))

    tpl.resource_count_is("AWS::ApiGateway::RestApi", 1)
    tpl.has_resource_properties(
        "AWS::ApiGateway::RestApi", Match.object_like({"Name": "tendril-dev-client-api"})
    )

    # Container image (ADR-0014) — no Handler/Runtime property, PackageType: Image instead.
    tpl.has_resource_properties(
        "AWS::Lambda::Function",
        Match.object_like(
            {
                "FunctionName": "tendril-dev-garden-handler",
                "PackageType": "Image",
                "Architectures": ["arm64"],
                "Environment": Match.object_like(
                    {
                        "Variables": Match.object_like(
                            {
                                "APP_TABLE_NAME": "tendril-dev-app",
                                "MEDIA_BUCKET_NAME": "tendril-dev-media",
                            }
                        )
                    }
                ),
            }
        ),
    )

    # TransactWriteItems isn't part of grant_read_write_data's action set; createGarden's/
    # createPlant's double-writes need it added explicitly (data-architecture.md §2).
    actions = set()
    for res in tpl.find_resources("AWS::IAM::Policy").values():
        for stmt in res["Properties"]["PolicyDocument"]["Statement"]:
            act = stmt["Action"]
            actions.update(act if isinstance(act, list) else [act])
    assert "dynamodb:TransactWriteItems" in actions
    # OB-02: createMediaUpload signs a presigned PUT URL — the signing role needs s3:PutObject.
    assert "s3:PutObject" in actions
    # WS-03: createGoal publishes goal.submitted via events:PutEvents.
    assert "events:PutEvents" in actions

    # API Gateway is granted permission to invoke the garden handler.
    tpl.has_resource_properties(
        "AWS::Lambda::Permission",
        Match.object_like(
            {"Action": "lambda:InvokeFunction", "Principal": "apigateway.amazonaws.com"}
        ),
    )


def test_client_api_stack_openapi_spec_covers_ob02_and_ws01_operations():
    app = _app()
    tpl = Template.from_stack(ClientApiStack(app, "capi2", env_name="dev", env=ENV))
    rest_apis = tpl.find_resources("AWS::ApiGateway::RestApi")
    (rest_api,) = rest_apis.values()
    body = rest_api["Properties"]["Body"]
    assert "/gardens/{gardenId}/media" in body["paths"]
    assert "/gardens/{gardenId}/plants" in body["paths"]
    assert "/gardens/{gardenId}/goals" in body["paths"]


# --- foundation (media bucket CORS for OB-02's direct-to-S3 upload) ---------


def test_media_bucket_allows_cross_origin_put():
    app = _app()
    tpl = Template.from_stack(FoundationStack(app, "fnd", env_name="dev", env=ENV))
    tpl.has_resource_properties(
        "AWS::S3::Bucket",
        Match.object_like(
            {
                "BucketName": "tendril-dev-media",
                "CorsConfiguration": {
                    "CorsRules": Match.array_with(
                        [Match.object_like({"AllowedMethods": ["PUT"], "AllowedOrigins": ["*"]})]
                    )
                },
            }
        ),
    )


# --- frontend (S3 static website hosting) -----------------------------------


def test_frontend_bucket_is_a_public_static_website():
    app = _app()
    tpl = Template.from_stack(FrontendStack(app, "fe", env_name="dev", env=ENV))
    tpl.resource_count_is("AWS::S3::Bucket", 1)
    tpl.has_resource_properties(
        "AWS::S3::Bucket",
        Match.object_like(
            {
                "BucketName": "tendril-dev-web",
                "WebsiteConfiguration": {
                    "IndexDocument": "index.html",
                    "ErrorDocument": "index.html",
                },
            }
        ),
    )
    # Public read via bucket policy (not ACLs) — BLOCK_ACLS still blocks legacy ACL grants.
    tpl.has_resource_properties(
        "AWS::S3::BucketPolicy",
        Match.object_like(
            {
                "PolicyDocument": Match.object_like(
                    {
                        "Statement": Match.array_with(
                            [
                                Match.object_like(
                                    {"Action": "s3:GetObject", "Principal": Match.any_value()}
                                )
                            ]
                        )
                    }
                )
            }
        ),
    )
