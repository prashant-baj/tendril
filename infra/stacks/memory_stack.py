"""Memory stack: a Bedrock Knowledge Base (S3 Vectors backend) for per-garden agent memory
(AF-05, ADR-0001 action item 6).

Deployed on its own, resolved by **stable name** (no CloudFormation cross-stack import) — the
same "resolve by name at runtime" posture already established for prompts/guardrails
(ADR-0008's deployment topology note). `agents/hello_agent/agent.py` looks up this knowledge
base's id and its data source's id via `bedrock-agent:ListKnowledgeBases`/`ListDataSources`,
matching by the stable names this stack assigns — never a hardcoded/exported id.

**S3 Vectors, not OpenSearch Serverless** (a real architectural choice, confirmed with the
project owner): OpenSearch Serverless bills a continuous minimum OCU cost even idle — the first
resource in this project that would work that way, since everything else (Lambda, DynamoDB
on-demand, S3, EventBridge) is pay-per-use. S3 Vectors (`aws_s3vectors`) is pay-per-request and
keeps that posture intact; confirmed available in this account/region via a live
`s3vectors list-vector-buckets` call before building this.

The knowledge base's data source is `CUSTOM` (inline-text ingestion via
`IngestKnowledgeBaseDocuments`), not `S3` — no S3 sidecar bucket/sync-job management, matching
`strands.vended_memory_stores.bedrock_knowledge_base`'s "simplest write path" for a
`BedrockKnowledgeBaseStore`. Embeddings use `amazon.titan-embed-text-v2:0` (1024 dimensions,
on-demand, confirmed available in this region) — no provisioned throughput to manage.

Per-specialist access (which agents can read/write this memory at all) is granted in
`AgentCoreStack`, scoped to only the specialists whose registry entry sets `memory.enabled`
(mirrors AF-03's per-specialist tool IAM scoping) — this stack only owns the resource itself.
"""

from aws_cdk import (
    CfnOutput,
    Stack,
    aws_bedrock as bedrock,
    aws_iam as iam,
    aws_s3vectors as s3vectors,
)
from constructs import Construct

# Titan Embed Text v2's default output dimensionality; must match the vector index exactly.
EMBEDDING_DIMENSION = 1024


class MemoryStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, env_name: str, **kwargs) -> None:
        super().__init__(scope, cid, **kwargs)
        prefix = f"tendril-{env_name}"
        embedding_model_arn = (
            f"arn:aws:bedrock:{self.region}::foundation-model/amazon.titan-embed-text-v2:0"
        )

        vector_bucket = s3vectors.CfnVectorBucket(
            self, "MemoryVectorBucket", vector_bucket_name=f"{prefix}-memory-vectors"
        )
        vector_index = s3vectors.CfnIndex(
            self,
            "MemoryVectorIndex",
            index_name=f"{prefix}-memory-index",
            vector_bucket_name=vector_bucket.vector_bucket_name,
            data_type="float32",
            dimension=EMBEDDING_DIMENSION,
            distance_metric="cosine",
        )
        vector_index.add_resource_dependency(vector_bucket)

        # The knowledge base's own service role — distinct from any agent's execution role
        # (which only gets grants to *call* the KB, added in AgentCoreStack).
        kb_role = iam.Role(
            self,
            "MemoryKnowledgeBaseRole",
            role_name=f"{prefix}-memory-kb",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="Service role for Tendril's per-garden memory Knowledge Base",
        )
        kb_role.add_to_policy(
            iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=[embedding_model_arn])
        )
        kb_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3vectors:GetIndex", "s3vectors:GetVectorBucket"],
                resources=[vector_bucket.attr_vector_bucket_arn, vector_index.attr_index_arn],
            )
        )
        kb_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3vectors:PutVectors",
                    "s3vectors:GetVectors",
                    "s3vectors:QueryVectors",
                    "s3vectors:ListVectors",
                    "s3vectors:DeleteVectors",
                ],
                resources=[vector_index.attr_index_arn],
            )
        )

        knowledge_base = bedrock.CfnKnowledgeBase(
            self,
            "MemoryKnowledgeBase",
            name=f"{prefix}-memory",
            role_arn=kb_role.role_arn,
            description="Per-garden long-term memory for Tendril specialist agents (AF-05).",
            knowledge_base_configuration=bedrock.CfnKnowledgeBase.KnowledgeBaseConfigurationProperty(
                type="VECTOR",
                vector_knowledge_base_configuration=bedrock.CfnKnowledgeBase.VectorKnowledgeBaseConfigurationProperty(
                    embedding_model_arn=embedding_model_arn,
                ),
            ),
            storage_configuration=bedrock.CfnKnowledgeBase.StorageConfigurationProperty(
                type="S3_VECTORS",
                s3_vectors_configuration=bedrock.CfnKnowledgeBase.S3VectorsConfigurationProperty(
                    vector_bucket_arn=vector_bucket.attr_vector_bucket_arn,
                    index_arn=vector_index.attr_index_arn,
                ),
            ),
        )
        knowledge_base.add_resource_dependency(vector_index)

        data_source = bedrock.CfnDataSource(
            self,
            "MemoryDataSource",
            knowledge_base_id=knowledge_base.attr_knowledge_base_id,
            name=f"{prefix}-memory-datasource",
            data_source_configuration=bedrock.CfnDataSource.DataSourceConfigurationProperty(
                type="CUSTOM",
            ),
        )

        CfnOutput(self, "MemoryKnowledgeBaseName", value=knowledge_base.name)
        CfnOutput(self, "MemoryKnowledgeBaseId", value=knowledge_base.attr_knowledge_base_id)
        CfnOutput(self, "MemoryDataSourceName", value=data_source.name)
