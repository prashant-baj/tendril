"""Central configuration loader.

Rules (see docs/engineering-best-practices.md):
- Non-secret config comes from environment variables (injected per environment).
- Secrets are NEVER read from code or committed files; they are fetched at runtime
  from AWS Secrets Manager / SSM Parameter Store via `get_secret`.
- No AWS account IDs, ARNs, or keys are hardcoded here.
"""
from __future__ import annotations

import os
from functools import lru_cache


class Config:
    """Non-secret runtime configuration, sourced from environment variables."""

    def __init__(self) -> None:
        self.env_name: str = os.getenv("ENV_NAME", "dev")
        self.aws_region: str = os.getenv("AWS_REGION", "ap-south-1")
        self.model_id: str | None = os.getenv("MODEL_ID")
        self.prompt_bucket: str | None = os.getenv("PROMPT_BUCKET")
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def is_prod(self) -> bool:
        return self.env_name == "prod"


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()


def get_secret(name: str) -> str:
    """Fetch a secret at runtime from AWS Secrets Manager. Never cache to disk.

    `name` is the secret's name/ARN — provided via configuration, not hardcoded.
    """
    import boto3  # imported lazily so unit tests need no AWS

    client = boto3.client("secretsmanager", region_name=get_config().aws_region)
    return client.get_secret_value(SecretId=name)["SecretString"]
