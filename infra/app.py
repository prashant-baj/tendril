#!/usr/bin/env python3
"""Tendril CDK agentic app.

Environment is selected by the `env_name` context (dev|prod); account/region come
from the CDK environment (CDK_DEFAULT_*). No account IDs are hardcoded.
"""

import os

import aws_cdk as cdk
from stacks.agentcore_stack import AgentCoreStack
from stacks.foundation_stack import FoundationStack
from stacks.frontend_stack import FrontendStack
from stacks.guardrails_stack import GuardrailsStack
from stacks.pipeline_stack import PipelineStack
from stacks.prompts_stack import PromptsStack

app = cdk.App()
env_name = app.node.try_get_context("env_name") or "dev"

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION"),
)

FoundationStack(app, f"tendril-{env_name}-foundation", env_name=env_name, env=env)
PromptsStack(app, f"tendril-{env_name}-prompts", env_name=env_name, env=env)
GuardrailsStack(app, f"tendril-{env_name}-guardrails", env_name=env_name, env=env)
AgentCoreStack(app, f"tendril-{env_name}-agentcore", env_name=env_name, env=env)
FrontendStack(app, f"tendril-{env_name}-frontend", env_name=env_name, env=env)
PipelineStack(app, f"tendril-{env_name}-pipeline", env_name=env_name, env=env)

cdk.Tags.of(app).add("project", "tendril")
cdk.Tags.of(app).add("env", env_name)

app.synth()
