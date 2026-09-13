"""AgentCore stack: IAM roles + registry-driven AgentCore Runtimes + the orchestrator Lambda
that invokes them, deployed via CDK.

Per ADR-0005, agent runtimes use the CDK L2 `aws_bedrockagentcore.Runtime` construct
(no imperative CLI deploy). By default the container image is built from the agent
folder (ARM64); pass context `agent_image_uri` to deploy a pre-built image instead
(used by CI / to synth without a local Docker build).

Per ADR-0006, prompts live in a separate `PromptsStack` and are referenced here only
by a **stable name** (no CloudFormation cross-stack import), so prompt changes deploy
independently. The runtime receives `PROMPT_NAME` and resolves the prompt at runtime.

Per ADR-0012, runtimes are now **registry-driven**: one `aws_bedrockagentcore.Runtime` per
`agents/registry/*.json` entry, replacing the single hardcoded `_agent_runtime()` call —
adding/removing a specialist is a registry-file change, not a stack-code change.

WS-02: the orchestrator Lambda (`app/orchestrator/`) and its EventBridge trigger live in
**this same stack**, not a separate one. AgentCore Runtime ARNs (unlike table/bucket names)
include an AWS-generated id and aren't predictable by naming convention, so they can't be
resolved cross-stack the "stable name" way ADR-0008 established for prompts/guardrails/tables.
Keeping the orchestrator here lets its environment reference `runtime.agent_runtime_arn`
directly as an in-memory CDK token — never crossing a stack boundary, so there's no need for a
CFN cross-stack export/import (exactly the "exported-value-in-use" trap ADR-0008 rejected).

Per AF-03, Tool APIs are likewise directory-driven: one Lambda + Function URL (IAM-authenticated)
per `app/tools/<name>/` folder, built the same way for the same reason Function URLs aren't
cross-stack-name-predictable either. Each specialist gets **its own** execution role (not one
role shared by every runtime) so `lambda:InvokeFunctionUrl` can be granted only for the tools its
own registry entry declares — the per-specialist IAM scoping AF-01 deferred until a real tool
existed to scope against.

Per AF-05, a registry entry's `memory` block (`{"enabled": true, "scope": "..."}`) grants that
same per-specialist role read/write access to `MemoryStack`'s Bedrock Knowledge Base — resolved
by stable name at runtime (`KNOWLEDGE_BASE_NAME`/`MEMORY_DATA_SOURCE_NAME`), same posture as
prompts/guardrails, never a cross-stack reference to the (separately deployed) memory stack.
"""

import json
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
    aws_bedrockagentcore as agentcore,
    aws_dynamodb as ddb,
    aws_ecr as ecr,
    aws_ecr_assets as ecr_assets,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
)
from constructs import Construct

AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"
REGISTRY_DIR = AGENTS_DIR / "registry"
ORCHESTRATOR_DIR = Path(__file__).resolve().parents[2] / "app" / "orchestrator"
TOOLS_DIR = Path(__file__).resolve().parents[2] / "app" / "tools"

# The Client API's goal-intake handler (WS-03) publishes goal.submitted events under this
# source; the orchestrator is the only subscriber for now (ADR-0012's async trigger flow).
# goal.message.received (PA-02) is the same source, published by the goal-message handler for
# every chat-thread turn after the first. task.checkin.received (PA-05) is the same source too,
# published by post_task_checkin after a check-in photo is attached to a task.
GOAL_EVENT_SOURCE = "tendril.client-api"
GOAL_SUBMITTED_DETAIL_TYPE = "goal.submitted"
GOAL_MESSAGE_RECEIVED_DETAIL_TYPE = "goal.message.received"
TASK_CHECKIN_RECEIVED_DETAIL_TYPE = "task.checkin.received"


class AgentCoreStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, env_name: str, **kwargs) -> None:
        super().__init__(scope, cid, **kwargs)
        prefix = f"tendril-{env_name}"
        self._prefix = prefix

        # --- Tool Lambdas: one per app/tools/<name>/ folder (AF-03) ---
        # Directory-driven the same way agents/registry, prompts/*.md, and guardrails/*.json
        # are — adding a tool is adding a folder, never a stack-code change.
        self.tools: dict[str, dict] = {}
        if TOOLS_DIR.is_dir():
            for tool_dir in sorted(p for p in TOOLS_DIR.iterdir() if p.is_dir()):
                name = tool_dir.name
                tool_cid = name.capitalize()
                image_repo = self.node.try_get_context(f"{name}_tool_image_repo")
                if image_repo:
                    repo = ecr.Repository.from_repository_name(
                        self, f"{tool_cid}ToolRepo", image_repo
                    )
                    image_tag = self.node.try_get_context(f"{name}_tool_image_tag") or "latest"
                    code = lambda_.DockerImageCode.from_ecr(repo, tag=image_tag)
                else:
                    code = lambda_.DockerImageCode.from_image_asset(
                        str(tool_dir), platform=ecr_assets.Platform.LINUX_ARM64
                    )
                fn = lambda_.DockerImageFunction(
                    self,
                    f"{tool_cid}Tool",
                    function_name=f"{prefix}-tool-{name}",
                    code=code,
                    architecture=lambda_.Architecture.ARM_64,
                    timeout=Duration.seconds(10),
                )
                # AWS_IAM (not NONE): a tool is invoked only by specialists whose registry entry
                # declares it — enforced below via a per-specialist grant, not left open to
                # anyone who obtains the URL.
                url = fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.AWS_IAM)
                self.tools[name] = {"function": fn, "url": url.url}
                CfnOutput(self, f"{tool_cid}ToolUrl", value=url.url)

        # --- AgentCore runtimes: one per agents/registry/*.json entry (ADR-0012) ---
        context_model_id = self.node.try_get_context("model_id") or ""
        image_uri = self.node.try_get_context("agent_image_uri")  # optional pre-built image

        # Keyed by each entry's own declared "name" (not the filename) — a registry file's name
        # on disk is not required to match its "name" field, so looking this up by filename
        # stem elsewhere would silently break the moment the two diverge.
        registry_entries = {
            entry["name"]: entry
            for entry in (
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(REGISTRY_DIR.glob("*.json"))
            )
        }
        self.runtimes: dict[str, agentcore.Runtime] = {}
        for entry in registry_entries.values():
            name = entry["name"]
            self.runtimes[name] = self._agent_runtime(
                name=name,
                folder=entry["template"],
                env_name=env_name,
                model_id=entry.get("model_id") or context_model_id,
                image_uri=image_uri,
                prompt_name=f"{prefix}-{entry['prompt_name']}",  # stable name; owned by PromptsStack
                guardrail_name=f"{prefix}-{entry['guardrail_name']}",  # owned by GuardrailsStack
                tool_names=entry.get("tools") or [],
                memory_config=entry.get("memory") or {},
            )

        for name, runtime in self.runtimes.items():
            CfnOutput(self, f"{name.capitalize()}RuntimeArn", value=runtime.agent_runtime_arn)

        # --- Orchestrator Lambda (WS-02 infra + WS-04 application: Strands agent loop) ---
        app_table = ddb.Table.from_table_name(self, "AppTable", f"{prefix}-app")
        media_bucket = s3.Bucket.from_bucket_name(self, "MediaBucket", f"{prefix}-media")

        orchestrator_image_repo = self.node.try_get_context("orchestrator_image_repo")
        if orchestrator_image_repo:
            repo = ecr.Repository.from_repository_name(
                self, "OrchestratorRepo", orchestrator_image_repo
            )
            orchestrator_image_tag = self.node.try_get_context("orchestrator_image_tag") or "latest"
            orchestrator_code = lambda_.DockerImageCode.from_ecr(repo, tag=orchestrator_image_tag)
        else:
            orchestrator_code = lambda_.DockerImageCode.from_image_asset(
                str(ORCHESTRATOR_DIR), platform=ecr_assets.Platform.LINUX_ARM64
            )

        # agent name -> {arn, description}, built from the SAME registry + the runtimes just
        # provisioned above (in-memory CDK tokens — never a cross-stack reference).
        agent_manifest = {
            name: {
                "arn": runtime.agent_runtime_arn,
                "description": registry_entries[name]["description"],
            }
            for name, runtime in self.runtimes.items()
        }

        self.orchestrator = lambda_.DockerImageFunction(
            self,
            "Orchestrator",
            function_name=f"{prefix}-orchestrator",
            code=orchestrator_code,
            architecture=lambda_.Architecture.ARM_64,
            timeout=Duration.minutes(5),
            memory_size=512,
            environment={
                "APP_TABLE_NAME": app_table.table_name,
                "MEDIA_BUCKET_NAME": media_bucket.bucket_name,
                "MODEL_ID": context_model_id,
                "LOG_LEVEL": "INFO",
                "AGENT_MANIFEST": self.to_json_string(agent_manifest),
            },
        )
        app_table.grant_read_write_data(self.orchestrator)
        # The orchestrator is ADR-0013's trusted-tier exception: it holds MediaBucket IAM so it
        # can generate a short-lived presigned GET URL for a goal's attached photo and pass
        # *that* to specialists (who get no S3 IAM at all) — never the bucket access itself.
        media_bucket.grant_read(self.orchestrator)
        self.orchestrator.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=["*"],
            )
        )
        # Scoped to the runtimes this deploy actually registered — never a wildcard (ADR-0012).
        # AgentCore authorizes InvokeAgentRuntime against the runtime-ENDPOINT sub-resource, not
        # the bare runtime ARN — confirmed via a live AccessDeniedException naming
        # ".../runtime/<id>/runtime-endpoint/DEFAULT" as the checked resource, not the runtime
        # ARN alone. Granting only the bare ARN (as originally written) 403s every real call;
        # both forms are included since only the endpoint suffix was actually verified as the
        # real requirement — the bare ARN can't hurt and covers any other API that does check it.
        self.orchestrator.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[
                    resource
                    for runtime in self.runtimes.values()
                    for resource in (
                        runtime.agent_runtime_arn,
                        f"{runtime.agent_runtime_arn}/runtime-endpoint/*",
                    )
                ],
            )
        )

        # EventBridge: goal.submitted -> orchestrator, asynchronous (ADR-0004/ADR-0012). The
        # Client API Lambda (WS-03) is never invoked synchronously by this rule; it only
        # publishes the event via events:PutEvents (granted in ClientApiStack).
        goal_submitted_rule = events.Rule(
            self,
            "GoalSubmittedRule",
            rule_name=f"{prefix}-goal-submitted",
            event_pattern=events.EventPattern(
                source=[GOAL_EVENT_SOURCE], detail_type=[GOAL_SUBMITTED_DETAIL_TYPE]
            ),
        )
        goal_submitted_rule.add_target(targets.LambdaFunction(self.orchestrator))

        # EventBridge: goal.message.received -> orchestrator (PA-02) — every chat-thread turn
        # after the first, mirroring GoalSubmittedRule exactly. No new IAM: the Client API
        # Lambda's events:PutEvents grant isn't scoped by detail-type.
        goal_message_received_rule = events.Rule(
            self,
            "GoalMessageReceivedRule",
            rule_name=f"{prefix}-goal-message-received",
            event_pattern=events.EventPattern(
                source=[GOAL_EVENT_SOURCE], detail_type=[GOAL_MESSAGE_RECEIVED_DETAIL_TYPE]
            ),
        )
        goal_message_received_rule.add_target(targets.LambdaFunction(self.orchestrator))

        # EventBridge: task.checkin.received -> orchestrator (PA-05) — a check-in photo was
        # attached to a task; mirrors GoalSubmittedRule/GoalMessageReceivedRule exactly. No new
        # IAM: the Client API Lambda's events:PutEvents grant isn't scoped by detail-type.
        task_checkin_received_rule = events.Rule(
            self,
            "TaskCheckinReceivedRule",
            rule_name=f"{prefix}-task-checkin-received",
            event_pattern=events.EventPattern(
                source=[GOAL_EVENT_SOURCE], detail_type=[TASK_CHECKIN_RECEIVED_DETAIL_TYPE]
            ),
        )
        task_checkin_received_rule.add_target(targets.LambdaFunction(self.orchestrator))

        CfnOutput(self, "OrchestratorArn", value=self.orchestrator.function_arn)

    def _make_agent_role(
        self, name: str, tool_names: list[str], memory_config: dict | None = None
    ) -> iam.Role:
        """One execution role per specialist (not one shared by every runtime) so
        `lambda:InvokeFunctionUrl` can be granted only for the tools *this* specialist's
        registry entry declares (AF-03) — a specialist can't invoke a tool it didn't ask for."""
        role = iam.Role(
            self,
            f"{name.capitalize()}ExecRole",
            role_name=f"{self._prefix}-{name}-exec",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description=f"Execution role for the '{name}' AgentCore runtime",
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=["*"],
            )
        )
        role.add_to_policy(
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
        # Resolve + read externalized prompts by name (ADR-0006). ListPrompts has no
        # resource; GetPrompt is scoped to this account/region's prompts.
        role.add_to_policy(iam.PolicyStatement(actions=["bedrock:ListPrompts"], resources=["*"]))
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:GetPrompt"],
                resources=[f"arn:aws:bedrock:{self.region}:{self.account}:prompt/*"],
            )
        )
        # Resolve + apply the externalized guardrail by name (ADR-0008). ListGuardrails has
        # no resource; GetGuardrail/ApplyGuardrail are scoped to this account/region's guardrails.
        role.add_to_policy(iam.PolicyStatement(actions=["bedrock:ListGuardrails"], resources=["*"]))
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:GetGuardrail", "bedrock:ApplyGuardrail"],
                resources=[f"arn:aws:bedrock:{self.region}:{self.account}:guardrail/*"],
            )
        )
        # AF-03: grant invoke only for the tools this specialist's own registry entry declares.
        for tool_name in tool_names:
            tool = self.tools.get(tool_name)
            if tool:
                tool["function"].grant_invoke_url(role)
        # AF-05: grant memory access only when this specialist's registry entry opts in
        # (`memory.enabled: true`). Wildcarded to the knowledge-base resource type, not a
        # specific id — MemoryStack deploys independently, so its real id isn't known here at
        # synth time (same posture as the prompt/guardrail wildcards above).
        #
        # All actions use the "bedrock:" IAM prefix, NOT "bedrock-agent:"/"bedrock-agent-runtime:"
        # (the boto3 *client* names) — confirmed via a real AccessDeniedException naming
        # "bedrock:ListKnowledgeBases" as the required action when this was first granted under
        # the (wrong) client-matching prefix. Same quirk as GetPrompt/GetGuardrail above, just not
        # applied here the first time.
        if (memory_config or {}).get("enabled"):
            role.add_to_policy(
                iam.PolicyStatement(
                    # GetKnowledgeBase: the store calls this once at agent-construction time to
                    # detect the KB's type (confirmed via a real AccessDeniedException — missed
                    # on the first pass since it's an internal `initialize()` call, not one this
                    # code calls directly).
                    actions=["bedrock:Retrieve", "bedrock:GetKnowledgeBase"],
                    resources=[f"arn:aws:bedrock:{self.region}:{self.account}:knowledge-base/*"],
                )
            )
            role.add_to_policy(
                iam.PolicyStatement(
                    # StartIngestionJob/GetIngestionJob: confirmed via a real AccessDeniedException
                    # that IngestKnowledgeBaseDocuments (even for a CUSTOM/inline-text data source,
                    # no S3 sync involved) is enforced against the underlying ingestion-job actions,
                    # not just its own API-level action name — another instance of the action-name
                    # vs. IAM-permission mismatch already seen with ListKnowledgeBases/GetKnowledgeBase.
                    actions=[
                        "bedrock:IngestKnowledgeBaseDocuments",
                        "bedrock:StartIngestionJob",
                        "bedrock:GetIngestionJob",
                    ],
                    resources=[f"arn:aws:bedrock:{self.region}:{self.account}:knowledge-base/*"],
                )
            )
            role.add_to_policy(
                iam.PolicyStatement(
                    actions=["bedrock:ListKnowledgeBases", "bedrock:ListDataSources"],
                    resources=["*"],  # ListX actions have no ARN resource type to scope to
                )
            )
        return role

    def _agent_runtime(
        self,
        *,
        name: str,
        folder: str,
        env_name: str,
        model_id: str,
        image_uri: str | None,
        prompt_name: str,
        guardrail_name: str,
        tool_names: list[str],
        memory_config: dict | None = None,
    ) -> agentcore.Runtime:
        if image_uri:
            artifact = agentcore.AgentRuntimeArtifact.from_image_uri(image_uri)
        else:
            artifact = agentcore.AgentRuntimeArtifact.from_asset(
                str(AGENTS_DIR / folder),
                file="Dockerfile",
                platform=ecr_assets.Platform.LINUX_ARM64,
            )
        role = self._make_agent_role(name, tool_names, memory_config)
        # Only the endpoints for tools THIS specialist declares — matches the IAM grant above,
        # so the template agent never even sees a URL it has no permission to call.
        tool_endpoints = {
            tool_name: self.tools[tool_name]["url"]
            for tool_name in tool_names
            if tool_name in self.tools
        }
        memory_config = memory_config or {}
        environment_variables = {
            "MODEL_ID": model_id,
            "LOG_LEVEL": "INFO",
            "PROMPT_NAME": prompt_name,
            "GUARDRAIL_NAME": guardrail_name,
            "TOOLS": self.to_json_string(tool_names),
            "TOOL_ENDPOINTS": self.to_json_string(tool_endpoints),
        }
        if memory_config.get("enabled"):
            environment_variables["MEMORY_ENABLED"] = "true"
            environment_variables["KNOWLEDGE_BASE_NAME"] = f"{self._prefix}-memory"
            environment_variables["MEMORY_DATA_SOURCE_NAME"] = f"{self._prefix}-memory-datasource"
            environment_variables["MEMORY_SCOPE"] = memory_config.get("scope") or "garden"
        return agentcore.Runtime(
            self,
            f"{name.capitalize()}Runtime",
            runtime_name=f"tendril_{env_name}_{name}",  # [a-zA-Z0-9_] only
            agent_runtime_artifact=artifact,
            execution_role=role,
            environment_variables=environment_variables,
            network_configuration=agentcore.RuntimeNetworkConfiguration.using_public_network(),
            # Managed trace delivery requires the account's X-Ray trace segment
            # destination to be CloudWatch Logs:
            #   aws xray update-trace-segment-destination --destination CloudWatchLogs --region <region>
            # Default off to avoid a deploy failure; enable with `--context tracing=true`.
            tracing_enabled=(self.node.try_get_context("tracing") or "false").lower() == "true",
        )
