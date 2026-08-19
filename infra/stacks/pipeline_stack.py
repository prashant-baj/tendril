"""Pipeline stack: GitHub Actions OIDC deploy role (no static keys).

CI assumes this role via OIDC and then assumes the CDK bootstrap roles to
deploy. Set `github_org` / `github_repo` via context (cdk.json) — do not
hardcode. See docs/engineering-best-practices.md (GitOps).
"""

from aws_cdk import (
    CfnOutput,
    Stack,
    aws_iam as iam,
)
from constructs import Construct


class PipelineStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, env_name: str, **kwargs) -> None:
        super().__init__(scope, cid, **kwargs)
        prefix = f"tendril-{env_name}"
        org = self.node.try_get_context("github_org") or "YOUR_GH_ORG"
        repo = self.node.try_get_context("github_repo") or "tendril"

        # Reuse an existing GitHub OIDC provider if the account already has one
        # (pass its ARN via context `github_oidc_provider_arn`); otherwise create it.
        existing_arn = self.node.try_get_context("github_oidc_provider_arn")
        if existing_arn:
            provider = iam.OpenIdConnectProvider.from_open_id_connect_provider_arn(
                self, "GithubOidc", existing_arn
            )
        else:
            provider = iam.OpenIdConnectProvider(
                self,
                "GithubOidc",
                url="https://token.actions.githubusercontent.com",
                client_ids=["sts.amazonaws.com"],
            )

        deploy_role = iam.Role(
            self,
            "GithubDeployRole",
            role_name=f"{prefix}-gh-deploy",
            assumed_by=iam.WebIdentityPrincipal(
                provider.open_id_connect_provider_arn,
                {
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
                    },
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": f"repo:{org}/{repo}:*"
                    },
                },
            ),
            description="Assumed by GitHub Actions via OIDC to run cdk deploy",
        )

        # CI only needs to assume the CDK bootstrap roles + read CloudFormation.
        deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=["arn:aws:iam::*:role/cdk-*"],
            )
        )
        deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "cloudformation:DescribeStacks",
                    "cloudformation:GetTemplate",
                ],
                resources=["*"],
            )
        )

        CfnOutput(self, "GithubDeployRoleArn", value=deploy_role.role_arn)
