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
from stacks.memory_stack import MemoryStack  # noqa: E402
from stacks.pipeline_stack import PipelineStack  # noqa: E402
from stacks.prompts_stack import PromptsStack  # noqa: E402

ENV = Environment(account="123456789012", region="ap-south-1")
IMAGE = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/tendril:latest"


def _app(**extra_context) -> App:
    # agent_image_uri / garden_handler_image_repo / orchestrator_image_repo / *_tool_image_repo
    # avoid a Docker build during synth (ADR-0005, ADR-0014).
    return App(
        context={
            "agent_image_uri": IMAGE,
            "model_id": "global.amazon.nova-2-lite-v1:0",
            "garden_handler_image_repo": "tendril-dev-garden-handler",
            "orchestrator_image_repo": "tendril-dev-orchestrator",
            "tracker_image_repo": "tendril-dev-tracker",
            "weather_tool_image_repo": "tendril-dev-tool-weather",
            **extra_context,
        }
    )


# --- guardrails --------------------------------------------------------------
# Static policy-file checks (description/topic/messaging length limits Bedrock enforces
# that cdk synth doesn't catch) live in tests/test_guardrail_policy.py, generalized over
# every guardrails/*.json file. The tests below are CDK-synth assertions instead.


def test_guardrail_resource_and_policies():
    app = _app()
    tpl = Template.from_stack(GuardrailsStack(app, "gr", env_name="dev", env=ENV))
    # One per guardrails/*.json: vision (WS-04) + agronomy/irrigation/pest-disease/pruning
    # (configured 2026-09-13, ahead of PA-01's structured plan proposal).
    tpl.resource_count_is("AWS::Bedrock::Guardrail", 5)
    tpl.resource_count_is("AWS::Bedrock::GuardrailVersion", 5)
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
    # One per PROMPT_CATALOG entry: vision (WS-04) + agronomy/irrigation/pest-disease/pruning
    # (configured 2026-09-13, ahead of PA-01's structured plan proposal).
    tpl.resource_count_is("AWS::Bedrock::Prompt", 5)
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


# --- memory (AF-05: Bedrock Knowledge Base on S3 Vectors) --------------------


def test_memory_stack_provisions_knowledge_base_on_s3_vectors():
    app = _app()
    tpl = Template.from_stack(MemoryStack(app, "mem", env_name="dev", env=ENV))

    tpl.has_resource_properties(
        "AWS::Bedrock::KnowledgeBase",
        Match.object_like(
            {
                "Name": "tendril-dev-memory",
                "KnowledgeBaseConfiguration": Match.object_like({"Type": "VECTOR"}),
                "StorageConfiguration": Match.object_like({"Type": "S3_VECTORS"}),
            }
        ),
    )
    tpl.has_resource_properties(
        "AWS::Bedrock::DataSource",
        Match.object_like(
            {
                "Name": "tendril-dev-memory-datasource",
                "DataSourceConfiguration": Match.object_like({"Type": "CUSTOM"}),
            }
        ),
    )
    # S3 Vectors (pay-per-request), never OpenSearch Serverless (continuous cost) — the
    # project's other resources are all pay-per-use, and this was a deliberate choice to match.
    tpl.resource_count_is("AWS::S3Vectors::VectorBucket", 1)
    tpl.has_resource_properties(
        "AWS::S3Vectors::Index",
        Match.object_like({"Dimension": 1024, "DistanceMetric": "cosine"}),
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


# --- Memory: per-specialist Bedrock Knowledge Base access (AF-05) ------------


def test_specialist_with_memory_enabled_receives_env_vars():
    app = _app()
    tpl = Template.from_stack(AgentCoreStack(app, "ac9", env_name="dev", env=ENV))

    # vision.json declares memory.enabled: true — its runtime resolves the KB by stable name.
    tpl.has_resource_properties(
        "AWS::BedrockAgentCore::Runtime",
        Match.object_like(
            {
                "EnvironmentVariables": Match.object_like(
                    {
                        "MEMORY_ENABLED": "true",
                        "KNOWLEDGE_BASE_NAME": "tendril-dev-memory",
                        "MEMORY_DATA_SOURCE_NAME": "tendril-dev-memory-datasource",
                        "MEMORY_SCOPE": "garden",
                    }
                )
            }
        ),
    )


def test_memory_iam_scoped_to_the_specialist_that_enables_it():
    import json
    from pathlib import Path

    # A fixture specialist without a memory block — proves the grant is per-specialist, not
    # handed to every runtime, mirroring AF-03's tool-scoping test.
    registry_dir = Path(__file__).resolve().parents[2] / "agents" / "registry"
    fixture = registry_dir / "_test_fixture_no_memory_agent.json"
    fixture.write_text(
        json.dumps(
            {
                "name": "fixturenomemory",
                "template": "hello_agent",
                "model_id": "",
                "prompt_name": "vision-system",
                "guardrail_name": "vision-guardrail",
                "description": "test fixture without memory enabled",
                "tools": [],
            }
        ),
        encoding="utf-8",
    )
    try:
        app = _app()
        tpl = Template.from_stack(AgentCoreStack(app, "ac10", env_name="dev", env=ENV))

        granted_on = []
        for logical_id, res in tpl.find_resources("AWS::IAM::Policy").items():
            for stmt in res["Properties"]["PolicyDocument"]["Statement"]:
                act = stmt["Action"]
                actions = act if isinstance(act, list) else [act]
                if "bedrock:Retrieve" in actions:
                    granted_on.append(logical_id)

        assert any("Vision" in lid for lid in granted_on), (
            "vision (memory.enabled) should have bedrock:Retrieve"
        )
        assert not any("Fixturenomemory" in lid for lid in granted_on), (
            "a specialist without memory.enabled must not get the grant"
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
    # PA-02: every chat-thread turn after the first mirrors goal.submitted's rule exactly.
    tpl.has_resource_properties(
        "AWS::Events::Rule",
        Match.object_like(
            {
                "EventPattern": {
                    "source": ["tendril.client-api"],
                    "detail-type": ["goal.message.received"],
                }
            }
        ),
    )
    # PA-05: a task check-in also mirrors goal.submitted's rule exactly.
    tpl.has_resource_properties(
        "AWS::Events::Rule",
        Match.object_like(
            {
                "EventPattern": {
                    "source": ["tendril.client-api"],
                    "detail-type": ["task.checkin.received"],
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


# --- Phase 7.5+: tracker/scheduler + due-date follow-ups ---------------------


def test_tracker_lambda_and_schedule_provisioned():
    import json

    app = _app()
    tpl = Template.from_stack(AgentCoreStack(app, "ac10", env_name="dev", env=ENV))

    tpl.has_resource_properties(
        "AWS::Lambda::Function",
        Match.object_like(
            {
                "FunctionName": "tendril-dev-tracker",
                "PackageType": "Image",
                "Environment": Match.object_like(
                    {"Variables": Match.object_like({"APP_TABLE_NAME": "tendril-dev-app"})}
                ),
            }
        ),
    )
    tpl.has_resource_properties(
        "AWS::Scheduler::Schedule",
        Match.object_like(
            {
                "Name": "tendril-dev-tracker-tick",
                "ScheduleExpression": "rate(15 minutes)",
            }
        ),
    )

    # Regression test: app_table is Table.from_table_name() (an imported table with no index
    # metadata) — its grant_read_data() only grants base-table actions, NOT `.../index/*`,
    # confirmed live via a real AccessDeniedException on dynamodb:Query against TasksDueIndex.
    # The tracker needs an explicit IAM statement naming the index ARN.
    found_index_query_grant = False
    for res in tpl.find_resources("AWS::IAM::Policy").values():
        for stmt in res["Properties"]["PolicyDocument"]["Statement"]:
            act = stmt["Action"]
            actions = act if isinstance(act, list) else [act]
            if "dynamodb:Query" not in actions:
                continue
            resource = stmt["Resource"]
            resources = resource if isinstance(resource, list) else [resource]
            if any("TasksDueIndex" in json.dumps(r) for r in resources):
                found_index_query_grant = True
    assert found_index_query_grant, "tracker must have dynamodb:Query scoped to TasksDueIndex"


def test_followup_due_rule_targets_orchestrator():
    app = _app()
    tpl = Template.from_stack(AgentCoreStack(app, "ac11", env_name="dev", env=ENV))

    tpl.has_resource_properties(
        "AWS::Events::Rule",
        Match.object_like(
            {
                "EventPattern": {
                    "source": ["tendril.tracker"],
                    "detail-type": ["followup.due"],
                }
            }
        ),
    )


def test_orchestrator_receives_agent_state_bucket_env_and_iam():
    # Phase 7.5+: SnapshotSessionManager's own S3 backend (data-architecture.md §3.1).
    import json

    app = _app()
    tpl = Template.from_stack(AgentCoreStack(app, "ac12", env_name="dev", env=ENV))

    tpl.has_resource_properties(
        "AWS::Lambda::Function",
        Match.object_like(
            {
                "FunctionName": "tendril-dev-orchestrator",
                "Environment": Match.object_like(
                    {
                        "Variables": Match.object_like(
                            {"AGENT_STATE_BUCKET_NAME": "tendril-dev-agent-state"}
                        )
                    }
                ),
            }
        ),
    )

    # Distinguish "orchestrator has the agent-state bucket's IAM" from "orchestrator has the
    # media bucket's IAM" — both true, for different resources; a resource-string match proves
    # the grant is actually scoped to the new bucket, not just any s3:GetObject* anywhere.
    found_agent_state_grant = False
    for res in tpl.find_resources("AWS::IAM::Policy").values():
        for stmt in res["Properties"]["PolicyDocument"]["Statement"]:
            act = stmt["Action"]
            actions = act if isinstance(act, list) else [act]
            if any(a.startswith("s3:") for a in actions):
                resource = stmt["Resource"]
                resources = resource if isinstance(resource, list) else [resource]
                if any("agent-state" in json.dumps(r) for r in resources):
                    found_agent_state_grant = True
    assert found_agent_state_grant


def test_no_specialist_role_has_dynamodb_or_s3_iam_actions():
    # ADR-0013 Action Item 4 / data-architecture.md §9 item 6: specialists never get direct IAM
    # on AppTable/ConnectionsTable/MediaBucket/AgentStateBucket. Checked PER-ROLE (not aggregated
    # across every role in the stack, which would hide a violation on one specialist role behind
    # an unrelated pass on the orchestrator's own role) — must fail if a future change ever grants
    # a specialist dynamodb:/s3: directly.
    app = _app()
    tpl = Template.from_stack(AgentCoreStack(app, "ac13", env_name="dev", env=ENV))

    specialist_role_logical_ids = {
        logical_id
        for logical_id, res in tpl.find_resources("AWS::IAM::Role").items()
        if res["Properties"].get("RoleName", "").endswith("-exec")
    }
    assert specialist_role_logical_ids, "expected at least one specialist execution role"

    for logical_id, res in tpl.find_resources("AWS::IAM::Policy").items():
        role_refs = {
            r.get("Ref") for r in res["Properties"].get("Roles", []) if isinstance(r, dict)
        }
        if not role_refs & specialist_role_logical_ids:
            continue
        for stmt in res["Properties"]["PolicyDocument"]["Statement"]:
            act = stmt["Action"]
            actions = act if isinstance(act, list) else [act]
            for action in actions:
                assert not action.lower().startswith(("dynamodb:", "s3:")), (
                    f"specialist role policy {logical_id} grants {action!r}"
                )


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
    # PA-03: getGoalDetail signs a presigned GET url the same way — a real 403 was found live
    # (generate_presigned_url succeeds regardless of IAM; only the real fetch reveals the gap).
    assert "s3:GetObject*" in actions
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


def test_agent_state_bucket_provisioned():
    # Phase 7.5+: SnapshotSessionManager's own S3 backend, ADR-0013's trusted-tier exception.
    app = _app()
    tpl = Template.from_stack(FoundationStack(app, "fnd2", env_name="dev", env=ENV))
    tpl.has_resource_properties(
        "AWS::S3::Bucket", Match.object_like({"BucketName": "tendril-dev-agent-state"})
    )


def test_tasks_due_index_gsi_provisioned():
    # Phase 7.5+: the tracker/scheduler's defining query ("which tasks have a follow-up due").
    app = _app()
    tpl = Template.from_stack(FoundationStack(app, "fnd3", env_name="dev", env=ENV))
    tpl.has_resource_properties(
        "AWS::DynamoDB::Table",
        Match.object_like(
            {
                "TableName": "tendril-dev-app",
                "GlobalSecondaryIndexes": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "IndexName": "TasksDueIndex",
                                "KeySchema": [
                                    {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                                    {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                                ],
                            }
                        )
                    ]
                ),
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


# --- client API keys (ADR-0021, change add-api-key-auth) ----------------------

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch"}


def _client_api_template(env_name: str, **context) -> Template:
    app = _app(**context)
    return Template.from_stack(ClientApiStack(app, "capi-keys", env_name=env_name, env=ENV))


def _client_api_body(tpl: Template) -> dict:
    (rest_api,) = tpl.find_resources("AWS::ApiGateway::RestApi").values()
    return rest_api["Properties"]["Body"]


def _operations(body: dict):
    for path, item in body["paths"].items():
        for method, operation in item.items():
            if method in HTTP_METHODS and isinstance(operation, dict):
                yield path, method, operation


def _keys(tpl: Template) -> dict:
    """Logical id -> API key name."""
    return {
        lid: res["Properties"]["Name"]
        for lid, res in tpl.find_resources("AWS::ApiGateway::ApiKey").items()
    }


def _plans(tpl: Template) -> dict:
    """Logical id -> usage plan name."""
    return {
        lid: res["Properties"]["UsagePlanName"]
        for lid, res in tpl.find_resources("AWS::ApiGateway::UsagePlan").items()
    }


def _plan_of_key(tpl: Template, key_name: str) -> dict:
    """The usage plan properties of the one plan that contains the named key."""
    (key_lid,) = [lid for lid, name in _keys(tpl).items() if name == key_name]
    plan_lids = [
        res["Properties"]["UsagePlanId"]["Ref"]
        for res in tpl.find_resources("AWS::ApiGateway::UsagePlanKey").values()
        if res["Properties"]["KeyId"] == {"Ref": key_lid}
    ]
    assert len(plan_lids) == 1, f"{key_name} must be in exactly one usage plan"
    return tpl.find_resources("AWS::ApiGateway::UsagePlan")[plan_lids[0]]["Properties"]


def test_dev_has_exactly_one_frontend_key_and_one_eval_key():
    tpl = _client_api_template("dev")
    tpl.resource_count_is("AWS::ApiGateway::ApiKey", 2)
    assert sorted(_keys(tpl).values()) == ["tendril-dev-ci-eval", "tendril-dev-frontend"]
    tpl.resource_count_is("AWS::ApiGateway::UsagePlan", 2)


def test_prod_has_the_frontend_key_only_and_no_eval_key_or_plan():
    tpl = _client_api_template("prod")
    tpl.resource_count_is("AWS::ApiGateway::ApiKey", 1)
    tpl.resource_count_is("AWS::ApiGateway::UsagePlan", 1)
    assert list(_keys(tpl).values()) == ["tendril-prod-frontend"]
    assert list(_plans(tpl).values()) == ["tendril-prod-frontend"]


def test_prod_cannot_get_an_eval_key_through_context():
    tpl = _client_api_template("prod", eval_daily_quota="10", require_api_key="false")
    assert list(_keys(tpl).values()) == ["tendril-prod-frontend"]
    assert list(_plans(tpl).values()) == ["tendril-prod-frontend"]


def test_frontend_key_is_not_in_the_eval_keys_usage_plan():
    tpl = _client_api_template("dev")
    frontend_plan = _plan_of_key(tpl, "tendril-dev-frontend")
    eval_plan = _plan_of_key(tpl, "tendril-dev-ci-eval")
    assert frontend_plan["UsagePlanName"] == "tendril-dev-frontend"
    assert eval_plan["UsagePlanName"] == "tendril-dev-ci-eval-client-api"
    # Exactly one plan-key association per key: no plan holds both keys.
    plan_keys = tpl.find_resources("AWS::ApiGateway::UsagePlanKey").values()
    pairs = {
        (r["Properties"]["UsagePlanId"]["Ref"], r["Properties"]["KeyId"]["Ref"]) for r in plan_keys
    }
    assert len(pairs) == 2
    assert len({plan for plan, _ in pairs}) == 2


def test_usage_plans_have_throttle_and_daily_quota_with_context_overrides():
    tpl = _client_api_template("dev")
    for name in ("tendril-dev-frontend", "tendril-dev-ci-eval"):
        plan = _plan_of_key(tpl, name)
        assert plan["Throttle"]["RateLimit"] > 0
        assert plan["Throttle"]["BurstLimit"] > 0
        assert plan["Quota"]["Period"] == "DAY"
        assert plan["Quota"]["Limit"] > 0

    tpl2 = _client_api_template("dev", eval_daily_quota="123", eval_throttle_rate="7")
    plan = _plan_of_key(tpl2, "tendril-dev-ci-eval")
    assert plan["Quota"]["Limit"] == 123
    assert plan["Throttle"]["RateLimit"] == 7
    # The frontend plan is independent of the eval numbers.
    assert _plan_of_key(tpl2, "tendril-dev-frontend")["Quota"]["Limit"] != 123


def test_every_non_options_operation_requires_a_key_when_enforced():
    for env_name, context in (("dev", {"require_api_key": "true"}), ("prod", {})):
        body = _client_api_body(_client_api_template(env_name, **context))
        assert body["components"]["securitySchemes"]["api_key"] == {
            "type": "apiKey",
            "in": "header",
            "name": "x-api-key",
        }
        ops = list(_operations(body))
        assert any(m == "options" for _, m, _ in ops)
        assert any(m != "options" for _, m, _ in ops)
        for path, method, operation in ops:
            if method == "options":
                assert operation.get("security") == [], f"OPTIONS {path} must stay open"
            else:
                assert operation.get("security") == [{"api_key": []}], (
                    f"{env_name}: {method.upper()} {path} must require the key"
                )


def test_dev_does_not_enforce_the_key_by_default_so_the_first_deploy_is_safe():
    # Staged rollout: keys and plans exist, but no operation requires a key until
    # `require_api_key` is set to true after the front end carries the key.
    tpl = _client_api_template("dev")
    tpl.resource_count_is("AWS::ApiGateway::ApiKey", 2)
    for path, method, operation in _operations(_client_api_body(tpl)):
        if method != "options":
            assert not operation.get("security"), f"{method.upper()} {path} enforced too early"


def test_prod_enforces_even_if_the_flag_says_false():
    body = _client_api_body(_client_api_template("prod", require_api_key="false"))
    for path, method, operation in _operations(body):
        if method != "options":
            assert operation.get("security") == [{"api_key": []}], f"{method.upper()} {path}"


def test_no_key_value_appears_in_the_synthesized_template():
    import json
    import re

    for env_name in ("dev", "prod"):
        tpl = _client_api_template(env_name, require_api_key="true")
        for res in tpl.find_resources("AWS::ApiGateway::ApiKey").values():
            assert "Value" not in res["Properties"], "API Gateway must generate the key value"
        # Outputs expose the key ID (a Ref to the ApiKey resource) and nothing else.
        key_lids = set(_keys(tpl))
        outputs = tpl.to_json().get("Outputs", {})
        key_outputs = [o for name, o in outputs.items() if name.endswith("ApiKeyId")]
        assert key_outputs, "expected key id outputs"
        for out in key_outputs:
            assert out["Value"]["Ref"] in key_lids
        # Access logs may carry the key ID only: never the key value (`$context.identity.apiKey`
        # without the `Id` suffix) and never the x-api-key header.
        for stage in tpl.find_resources("AWS::ApiGateway::Stage").values():
            log_format = json.dumps(stage["Properties"].get("AccessLogSetting", {}))
            assert not re.search(r"\$context\.identity\.apiKey(?!Id)", log_format)
            assert "x-api-key" not in log_format.lower()


def test_cors_allows_x_api_key_on_preflight_and_gateway_responses():
    tpl = _client_api_template("dev")
    for path, method, operation in _operations(_client_api_body(tpl)):
        if method == "options":
            params = operation["x-amazon-apigateway-integration"]["responses"]["default"][
                "responseParameters"
            ]
            allowed = params["method.response.header.Access-Control-Allow-Headers"]
            assert "x-api-key" in allowed.lower(), f"OPTIONS {path}"

    origin_param = "gatewayresponse.header.Access-Control-Allow-Origin"
    headers_param = "gatewayresponse.header.Access-Control-Allow-Headers"
    for response_type in ("DEFAULT_4XX", "DEFAULT_5XX"):
        tpl.has_resource_properties(
            "AWS::ApiGateway::GatewayResponse",
            Match.object_like(
                {
                    "ResponseType": response_type,
                    "ResponseParameters": Match.object_like(
                        {origin_param: "'*'", headers_param: Match.string_like_regexp("x-api-key")}
                    ),
                }
            ),
        )


# --- eval-control placeholder (keeps the CI/EVAL key export, never deployed) -----------------

# The export the showcase-deployed `tendril-dev-eval-control` imports. If this name changes,
# CloudFormation blocks every client-api deploy (the 2026-11-23 failure).
LIVE_EVAL_KEY_EXPORT = "tendril-dev-client-api:ExportsOutputRefEvalApiKey50C01DA0E8FFFC6E"


def _dev_client_api_and_placeholder():
    from stacks.eval_control_stack import EvalControlPlaceholderStack

    app = _app()
    capi = ClientApiStack(app, "tendril-dev-client-api", env_name="dev", env=ENV)
    placeholder = EvalControlPlaceholderStack(
        app,
        "tendril-dev-eval-control",
        env_name="dev",
        eval_api_key=capi.eval_api_key,
        env=ENV,
    )
    return Template.from_stack(capi).to_json(), Template.from_stack(placeholder).to_json()


def test_client_api_keeps_the_exact_eval_key_export_the_live_eval_stack_imports():
    capi, placeholder = _dev_client_api_and_placeholder()
    exports = {o["Export"]["Name"]: o["Value"] for o in capi["Outputs"].values() if "Export" in o}
    assert exports == {LIVE_EVAL_KEY_EXPORT: {"Ref": "EvalApiKey50C01DA0"}}
    imports = [o["Value"] for o in placeholder["Outputs"].values()]
    assert {"Fn::ImportValue": LIVE_EVAL_KEY_EXPORT} in imports


def test_api_key_logical_ids_match_the_live_dev_stack():
    # Renaming a construct would replace the deployed key (new value) and break the import.
    capi, _ = _dev_client_api_and_placeholder()
    resources = capi["Resources"]
    for lid, rtype in {
        "FrontendApiKeyEAB75524": "AWS::ApiGateway::ApiKey",
        "EvalApiKey50C01DA0": "AWS::ApiGateway::ApiKey",
        "FrontendUsagePlanA5A486AD": "AWS::ApiGateway::UsagePlan",
        "EvalClientApiUsagePlanA8600D80": "AWS::ApiGateway::UsagePlan",
    }.items():
        assert resources[lid]["Type"] == rtype, lid


def test_placeholder_cannot_be_deployed():
    _, placeholder = _dev_client_api_and_placeholder()
    param = placeholder["Parameters"]["DoNotDeployPlaceholder"]
    # Required (no default) and unsatisfiable: CloudFormation rejects any deploy at validation.
    assert "Default" not in param
    assert param["MinLength"] == 1
    assert param["AllowedPattern"] == "^(?!)$"
    assert "DO NOT DEPLOY" in placeholder["Description"]
    # Nothing real in it: one no-op marker, no Lambda, no API.
    assert {r["Type"] for r in placeholder["Resources"].values()} == {
        "AWS::CloudFormation::WaitConditionHandle"
    }


def test_placeholder_is_dev_only():
    from stacks.eval_control_stack import EvalControlPlaceholderStack

    app = _app()
    capi = ClientApiStack(app, "tendril-prod-client-api", env_name="prod", env=ENV)
    with pytest.raises(ValueError):
        EvalControlPlaceholderStack(
            app, "tendril-prod-eval-control", env_name="prod", eval_api_key=None, env=ENV
        )
    assert capi.eval_api_key is None


def test_ci_never_deploys_the_eval_control_placeholder():
    from pathlib import Path

    import yaml

    ci_path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    ci = yaml.safe_load(ci_path.read_text(encoding="utf-8"))
    deploy_cmds = [
        step.get("run", "")
        for job in ci["jobs"].values()
        for step in job.get("steps", [])
        if "cdk deploy" in step.get("run", "")
    ]
    assert deploy_cmds, "expected at least one cdk deploy step"
    for cmd in deploy_cmds:
        assert "eval-control" not in cmd
        assert "--all" not in cmd, (
            "an --all deploy would deploy the placeholder over the live stack"
        )
