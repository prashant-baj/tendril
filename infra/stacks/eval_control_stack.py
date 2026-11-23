"""Placeholder for `tendril-dev-eval-control` (ADR-0021 decision 2 / ADR-0019). DEV ONLY. NEVER DEPLOYED.

The real Evaluation Control API stack was deployed to dev from the GitLab `showcase` /
`tendril-group/tendril` repo (`infra/stacks/eval_control_stack.py` there): a dev-only REST API, the
`tendril-dev-eval-handler` Lambda and a usage plan holding the CI/EVAL key. That stack **imports**
the CI/EVAL key id from `tendril-dev-client-api` (export
`tendril-dev-client-api:ExportsOutputRefEvalApiKey50C01DA0E8FFFC6E`). CloudFormation refuses to
update `tendril-dev-client-api` in any way that drops an export while another stack imports it.

This placeholder keeps that link without porting the evaluation harness yet: it references
`ClientApiStack.eval_api_key`, so CDK keeps generating exactly the same export (same construct id,
same `Ref`, so the same export name) whenever `tendril-dev-client-api` is synthesized.

It must **never be deployed**: deploying this template over the live stack would delete the real
evaluation API and handler. Two guards:

1. CI never deploys it. `.github/workflows/ci.yml` names its stacks explicitly and this one is not in
   the list (enforced by `infra/tests/test_stacks.py`). CDK deploys a listed stack's dependencies,
   not its dependents, so deploying `tendril-dev-client-api` does not pull this one in.
2. Its template declares a required parameter whose pattern can never match. A manual
   `cdk deploy tendril-dev-eval-control` fails at CloudFormation parameter validation before any
   change set is created, so the live resources are never touched.

Replace this file with the real stack (and port `app/eval_api`, the clock seam and ADR-0019/0020)
when the evaluation harness moves to GitHub Actions. Retiring evaluation instead means deleting the
live `tendril-dev-eval-control` stack first, then this placeholder and the eval key.
"""

from aws_cdk import (
    CfnOutput,
    CfnParameter,
    CfnWaitConditionHandle,
    Stack,
    aws_apigateway as apigateway,
)
from constructs import Construct

STACK_SUFFIX = "eval-control"
# Matches nothing: `^(?!)$` is an always-failing lookahead, and MinLength 1 rejects the empty
# string, so no value can ever satisfy this parameter.
NEVER_MATCHES = "^(?!)$"


class EvalControlPlaceholderStack(Stack):
    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        env_name: str,
        eval_api_key: apigateway.IApiKey | None,
        **kwargs,
    ) -> None:
        if env_name != "dev":
            raise ValueError(f"the eval-control placeholder is dev only, got {env_name!r}")
        if eval_api_key is None:
            raise ValueError("the eval-control placeholder needs the dev CI/EVAL key")
        super().__init__(
            scope,
            cid,
            description=(
                "PLACEHOLDER - DO NOT DEPLOY. Keeps the CI/EVAL key export referenced; the live "
                "stack is deployed from the showcase repo (ADR-0021)."
            ),
            **kwargs,
        )

        guard = CfnParameter(
            self,
            "DoNotDeployPlaceholder",
            type="String",
            min_length=1,
            allowed_pattern=NEVER_MATCHES,
            constraint_description=(
                "This is a placeholder stack in the GitHub repo and must never be deployed: it "
                "would delete the live Evaluation Control API. See infra/stacks/eval_control_stack.py."
            ),
            description="Guard: no value is valid, so CloudFormation always rejects a deploy.",
        )
        # A template needs at least one resource; a WaitConditionHandle is the standard no-op.
        # It also references the guard so template validation doesn't flag it as unused.
        marker = CfnWaitConditionHandle(self, "PlaceholderMarker")
        marker.add_metadata("guard", guard.value_as_string)

        # The reference that keeps `tendril-dev-client-api`'s export alive.
        CfnOutput(self, "EvalApiKeyIdImport", value=eval_api_key.key_id)
