"""Guardrails stack: Bedrock Guardrails (policy-as-data), deployed on its own.

Per ADR-0008 (layered defense-in-depth), Bedrock Guardrails are the *managed* safety
layer. Like prompts (ADR-0006), guardrail policies are externalized: the *what* lives as
JSON in `guardrails/`, and this stack is the *how* — it loads each policy file and creates
an `AWS::Bedrock::Guardrail` + a published version via CDK.

Keeping guardrails in a dedicated stack means a policy change deploys independently
(`cdk deploy tendril-<env>-guardrails`) without touching the runtime/foundation stacks.
Agents reference a guardrail by a **stable name** (no CloudFormation cross-stack import),
resolving id + version at runtime — so this stack stays fully decoupled.

Per AF-01 (docs/stories/agent-factory.md), this loops over every `guardrails/*.json` file
instead of a single hardcoded `hello_guardrail.json` — adding a specialist's guardrail is
adding a policy file, not a stack-code change. The construct id for the existing `hello`
policy is kept exactly as it was (`HelloGuardrail`/`HelloGuardrailVersion`) so this refactor
updates that resource in place rather than replacing it.
"""

import json
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Stack,
    aws_bedrock as bedrock,
)
from constructs import Construct

GUARDRAILS_DIR = Path(__file__).resolve().parents[2] / "guardrails"

# policy["name"] (e.g. "hello-guardrail") -> the CDK construct id prefix to use for it. Kept
# explicit (not derived automatically) so the pre-existing `hello` entry's construct ids never
# change — anything not listed here gets an automatically-derived id from its policy name.
CONSTRUCT_ID_OVERRIDES = {
    "hello-guardrail": "Hello",
}


def _construct_id(policy_name: str) -> str:
    if policy_name in CONSTRUCT_ID_OVERRIDES:
        return CONSTRUCT_ID_OVERRIDES[policy_name]
    return "".join(part.capitalize() for part in policy_name.replace("_", "-").split("-"))


class GuardrailsStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, env_name: str, **kwargs) -> None:
        super().__init__(scope, cid, **kwargs)
        prefix = f"tendril-{env_name}"

        self.guardrails: dict[str, bedrock.CfnGuardrail] = {}
        for path in sorted(GUARDRAILS_DIR.glob("*.json")):
            policy = json.loads(path.read_text(encoding="utf-8"))
            policy_cid = _construct_id(policy["name"])

            guardrail = bedrock.CfnGuardrail(
                self,
                f"{policy_cid}Guardrail",
                name=f"{prefix}-{policy['name']}",
                description=policy.get("description"),
                blocked_input_messaging=policy["blockedInputMessaging"],
                blocked_outputs_messaging=policy["blockedOutputsMessaging"],
                content_policy_config=self._content_policy(policy),
                topic_policy_config=self._topic_policy(policy),
                sensitive_information_policy_config=self._pii_policy(policy),
            )
            bedrock.CfnGuardrailVersion(
                self,
                f"{policy_cid}GuardrailVersion",
                guardrail_identifier=guardrail.attr_guardrail_id,
                description="Published version snapshot.",
            )
            self.guardrails[policy["name"]] = guardrail
            CfnOutput(self, f"{policy_cid}GuardrailName", value=guardrail.name)
            CfnOutput(self, f"{policy_cid}GuardrailId", value=guardrail.attr_guardrail_id)

        # Back-compat alias — existing code (AgentCoreStack) doesn't reference this, but keeps
        # the pre-refactor attribute name available in case anything external still does.
        self.hello_guardrail = self.guardrails["hello-guardrail"]

    @staticmethod
    def _content_policy(policy: dict) -> bedrock.CfnGuardrail.ContentPolicyConfigProperty | None:
        filters = (policy.get("contentPolicy") or {}).get("filters") or []
        if not filters:
            return None
        return bedrock.CfnGuardrail.ContentPolicyConfigProperty(
            filters_config=[
                bedrock.CfnGuardrail.ContentFilterConfigProperty(
                    type=f["type"],
                    input_strength=f["inputStrength"],
                    output_strength=f["outputStrength"],
                )
                for f in filters
            ]
        )

    @staticmethod
    def _topic_policy(policy: dict) -> bedrock.CfnGuardrail.TopicPolicyConfigProperty | None:
        topics = (policy.get("topicPolicy") or {}).get("topics") or []
        if not topics:
            return None
        return bedrock.CfnGuardrail.TopicPolicyConfigProperty(
            topics_config=[
                bedrock.CfnGuardrail.TopicConfigProperty(
                    name=t["name"],
                    type=t["type"],
                    definition=t["definition"],
                    examples=t.get("examples"),
                )
                for t in topics
            ]
        )

    @staticmethod
    def _pii_policy(
        policy: dict,
    ) -> bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty | None:
        entities = (policy.get("sensitiveInformationPolicy") or {}).get("piiEntities") or []
        if not entities:
            return None
        return bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
            pii_entities_config=[
                bedrock.CfnGuardrail.PiiEntityConfigProperty(
                    type=e["type"],
                    action=e["action"],
                )
                for e in entities
            ]
        )
