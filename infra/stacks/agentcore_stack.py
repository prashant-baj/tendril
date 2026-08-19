"""AgentCore stack: IAM roles + AgentCore Runtimes, deployed via CDK.

Per ADR-0005, agent runtimes are provisioned with the CDK L2
`aws_bedrockagentcore.Runtime` construct (no imperative CLI deploy). By default
the container image is built from the agent folder (ARM64); pass context
`agent_image_uri` to deploy a pre-built image instead (used by CI / to synth
without a local Docker build).
"""

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Stack,
    aws_bedrockagentcore as agentcore,
    aws_ecr_assets as ecr_assets,
    aws_iam as iam,
)
from constructs import Construct

AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"


class AgentCoreStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, env_name: str, **kwargs) -> None:
        super().__init__(scope, cid, **kwargs)
        prefix = f"tendril-{env_name}"

        # --- Execution role assumed by AgentCore runtimes ---
        self.exec_role = iam.Role(
            self,
            "AgentExecRole",
            role_name=f"{prefix}-agent-exec",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="Execution role for Tendril AgentCore runtimes",
        )
        self.exec_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=["*"],
            )
        )
        self.exec_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                ],
                resources=["*"],
            )
        )

        # --- AgentCore runtimes (CDK-managed) ---
        model_id = self.node.try_get_context("model_id") or ""
        image_uri = self.node.try_get_context("agent_image_uri")  # optional pre-built image

        self.hello_runtime = self._agent_runtime(
            name="hello",
            folder="hello_agent",
            env_name=env_name,
            model_id=model_id,
            image_uri=image_uri,
        )

        CfnOutput(self, "AgentExecRoleArn", value=self.exec_role.role_arn)
        CfnOutput(self, "HelloRuntimeArn", value=self.hello_runtime.agent_runtime_arn)

    def _agent_runtime(
        self,
        *,
        name: str,
        folder: str,
        env_name: str,
        model_id: str,
        image_uri: str | None,
    ) -> agentcore.Runtime:
        if image_uri:
            artifact = agentcore.AgentRuntimeArtifact.from_image_uri(image_uri)
        else:
            artifact = agentcore.AgentRuntimeArtifact.from_asset(
                str(AGENTS_DIR / folder),
                file="Dockerfile",
                platform=ecr_assets.Platform.LINUX_ARM64,
            )
        return agentcore.Runtime(
            self,
            f"{name.capitalize()}Runtime",
            runtime_name=f"tendril_{env_name}_{name}",  # [a-zA-Z0-9_] only
            agent_runtime_artifact=artifact,
            execution_role=self.exec_role,
            environment_variables={
                "MODEL_ID": model_id,
                "LOG_LEVEL": "INFO",
            },
            network_configuration=agentcore.RuntimeNetworkConfiguration.using_public_network(),
            # Managed trace delivery requires the account's X-Ray trace segment
            # destination to be CloudWatch Logs:
            #   aws xray update-trace-segment-destination --destination CloudWatchLogs --region <region>
            # Default off to avoid a deploy failure; enable with `--context tracing=true`.
            tracing_enabled=(self.node.try_get_context("tracing") or "false").lower() == "true",
        )
