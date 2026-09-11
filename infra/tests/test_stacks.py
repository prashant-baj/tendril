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
from stacks.frontend_stack import FrontendStack  # noqa: E402
from stacks.guardrails_stack import GuardrailsStack  # noqa: E402
from stacks.prompts_stack import PromptsStack  # noqa: E402

ENV = Environment(account="123456789012", region="ap-south-1")
IMAGE = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/tendril:latest"


def _app() -> App:
    # agent_image_uri avoids a Docker build during synth.
    return App(context={"agent_image_uri": IMAGE, "model_id": "global.amazon.nova-2-lite-v1:0"})


# --- guardrails --------------------------------------------------------------


def test_guardrail_resource_and_policies():
    app = _app()
    tpl = Template.from_stack(GuardrailsStack(app, "gr", env_name="dev", env=ENV))
    tpl.resource_count_is("AWS::Bedrock::Guardrail", 1)
    tpl.resource_count_is("AWS::Bedrock::GuardrailVersion", 1)
    tpl.has_resource_properties(
        "AWS::Bedrock::Guardrail",
        {
            "Name": "tendril-dev-hello-guardrail",
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
    tpl.resource_count_is("AWS::Bedrock::Prompt", 1)
    tpl.has_resource_properties(
        "AWS::Bedrock::Prompt",
        Match.object_like(
            {
                "Name": "tendril-dev-hello-system",
                "Variants": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "TemplateConfiguration": {
                                    "Text": {"Text": Match.string_like_regexp("hello agent")}
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
                        "PROMPT_NAME": "tendril-dev-hello-system",
                        "GUARDRAIL_NAME": "tendril-dev-hello-guardrail",
                        "MODEL_ID": "global.amazon.nova-2-lite-v1:0",
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
