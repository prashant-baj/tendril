"""Prompts stack: externalized prompts (Bedrock Prompt Management), deployed on its own.

Per ADR-0006, prompts are the application content. Keeping them in a dedicated stack
means prompt changes deploy independently (`cdk deploy tendril-<env>-prompts`) without
touching the runtime/foundation stacks. Agents reference prompts by a **stable name**
(no CloudFormation cross-stack import), so this stack stays fully decoupled.
"""

from aws_cdk import (
    CfnOutput,
    Stack,
    aws_bedrock as bedrock,
)
from constructs import Construct

HELLO_SYSTEM_PROMPT = (
    "You are Tendril's hello agent. Confirm the runtime is alive and, if asked a "
    "gardening question, answer briefly. Keep responses short."
)


class PromptsStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, env_name: str, **kwargs) -> None:
        super().__init__(scope, cid, **kwargs)
        prefix = f"tendril-{env_name}"

        self.hello_prompt = bedrock.CfnPrompt(
            self,
            "HelloPrompt",
            name=f"{prefix}-hello-system",
            description="System prompt for the hello agent (externalized).",
            default_variant="default",
            variants=[
                bedrock.CfnPrompt.PromptVariantProperty(
                    name="default",
                    template_type="TEXT",
                    template_configuration=bedrock.CfnPrompt.PromptTemplateConfigurationProperty(
                        text=bedrock.CfnPrompt.TextPromptTemplateConfigurationProperty(
                            text=HELLO_SYSTEM_PROMPT
                        )
                    ),
                )
            ],
        )
        bedrock.CfnPromptVersion(
            self,
            "HelloPromptVersion",
            prompt_arn=self.hello_prompt.attr_arn,
            description="Published version snapshot.",
        )

        CfnOutput(self, "HelloPromptName", value=self.hello_prompt.name)
