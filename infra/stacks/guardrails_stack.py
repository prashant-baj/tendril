"""Guardrails stack: Bedrock Guardrails (policy-as-data), deployed on its own.

Per ADR-0008 (layered defense-in-depth), Bedrock Guardrails are the *managed* safety
layer. Like prompts (ADR-0006), guardrail policies are externalized: the *what* lives as
JSON in `guardrails/`, and this stack is the *how* — it loads each policy file and creates
an `AWS::Bedrock::Guardrail` + a published version via CDK.

Keeping guardrails in a dedicated stack means a policy change deploys independently
(`cdk deploy tendril-<env>-guardrails`) without touching the runtime/foundation stacks.
Agents reference a guardrail by a **stable name** (no CloudFormation cross-stack import),
resolving id + version at runtime — so this stack stays fully decoupled.
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


class GuardrailsStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, env_name: str, **kwargs) -> None:
        super().__init__(scope, cid, **kwargs)
        prefix = f"tendril-{env_name}"

        policy = json.loads((GUARDRAILS_DIR / "hello_guardrail.json").read_text())

        self.hello_guardrail = bedrock.CfnGuardrail(
            self,
            "HelloGuardrail",
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
            "HelloGuardrailVersion",
            guardrail_identifier=self.hello_guardrail.attr_guardrail_id,
            description="Published version snapshot.",
        )

        CfnOutput(self, "HelloGuardrailName", value=self.hello_guardrail.name)
        CfnOutput(self, "HelloGuardrailId", value=self.hello_guardrail.attr_guardrail_id)

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
