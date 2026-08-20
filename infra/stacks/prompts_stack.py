"""Prompts stack: externalized prompts (Bedrock Prompt Management), deployed on its own.

Per ADR-0006, prompts are the application content — authored as **files** under `prompts/`
(the *what*) and provisioned by this stack (the *how*), mirroring how guardrails are handled
(`guardrails/` <-> GuardrailsStack). Each prompt's template text is read from
`prompts/<logical-name>.md` (the file is named for the prompt); the CfnPrompt is named
`tendril-<env>-<logical-name>` and agents resolve it by that **stable name** — no hardcoded
prompt text and no CloudFormation cross-stack import, so prompt edits deploy independently
(`cdk deploy tendril-<env>-prompts`).
"""

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Stack,
    aws_bedrock as bedrock,
)
from constructs import Construct

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

# Registry of externalized prompts. Key = logical (env-agnostic) prompt name; the
# template text is read from `prompts/<key>.md`. `cid` is the CloudFormation construct
# id prefix (kept stable so redeploys update in place rather than replace).
PROMPT_CATALOG = {
    "hello-system": {
        "cid": "Hello",
        "description": "System prompt for the hello agent (externalized).",
    },
}


class PromptsStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, env_name: str, **kwargs) -> None:
        super().__init__(scope, cid, **kwargs)
        prefix = f"tendril-{env_name}"

        self.prompts: dict[str, bedrock.CfnPrompt] = {}
        for logical, cfg in PROMPT_CATALOG.items():
            text = (PROMPTS_DIR / f"{logical}.md").read_text(encoding="utf-8").strip()
            prompt = bedrock.CfnPrompt(
                self,
                f"{cfg['cid']}Prompt",
                name=f"{prefix}-{logical}",
                description=cfg["description"],
                default_variant="default",
                variants=[
                    bedrock.CfnPrompt.PromptVariantProperty(
                        name="default",
                        template_type="TEXT",
                        template_configuration=bedrock.CfnPrompt.PromptTemplateConfigurationProperty(
                            text=bedrock.CfnPrompt.TextPromptTemplateConfigurationProperty(
                                text=text
                            )
                        ),
                    )
                ],
            )
            bedrock.CfnPromptVersion(
                self,
                f"{cfg['cid']}PromptVersion",
                prompt_arn=prompt.attr_arn,
                description="Published version snapshot.",
            )
            self.prompts[logical] = prompt
            CfnOutput(self, f"{cfg['cid']}PromptName", value=prompt.name)
