"""Hello agent — minimal Strands + AgentCore runtime to validate deployment (TF-04).

Model id comes from the environment. The system prompt is externalized to Bedrock
Prompt Management (ADR-0006) in a separate CDK stack and referenced here only by a
stable NAME (PROMPT_NAME) — the agent resolves the prompt id at runtime, so prompt
changes deploy independently of this runtime. A built-in fallback keeps the agent
working if the prompt can't be loaded. Nothing is hardcoded for configuration.

The Bedrock Guardrail (ADR-0008, the managed safety layer) is likewise externalized
to a separate GuardrailsStack and referenced only by a stable NAME (GUARDRAIL_NAME):
the agent resolves its id at runtime and attaches it to the model so input/output are
filtered on every Converse call. Resolution is fail-open for the MVP (the agent still
runs if the guardrail can't be resolved) — production should fail-closed; the
deterministic layers beneath (IAM, hardcoded checks, HITL) do not depend on this.
"""

import logging
import os

import boto3
from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("tendril.hello_agent")

MODEL_ID = os.getenv("MODEL_ID")  # provided per-environment; never hardcoded
PROMPT_NAME = os.getenv("PROMPT_NAME")  # stable name owned by the prompts stack
GUARDRAIL_NAME = os.getenv("GUARDRAIL_NAME")  # stable name owned by the guardrails stack
AWS_REGION = os.getenv("AWS_REGION")

DEFAULT_SYSTEM_PROMPT = (
    "You are Tendril's hello agent. Confirm the runtime is alive and, if asked a "
    "gardening question, answer briefly. Keep responses short."
)


def _resolve_prompt_id(client, name: str) -> str | None:
    """Find a prompt's id by its stable name (Prompt Management has no name filter)."""
    token = None
    while True:
        resp = client.list_prompts(**({"nextToken": token} if token else {}))
        for summary in resp.get("promptSummaries", []):
            if summary.get("name") == name:
                return summary.get("id")
        token = resp.get("nextToken")
        if not token:
            return None


def load_system_prompt() -> str:
    """Resolve + fetch the system prompt from Bedrock Prompt Management (ADR-0006).

    Falls back to a built-in default so the agent never fails on prompt load.
    """
    if not PROMPT_NAME:
        return DEFAULT_SYSTEM_PROMPT
    try:
        client = boto3.client("bedrock-agent", region_name=AWS_REGION)
        prompt_id = _resolve_prompt_id(client, PROMPT_NAME)
        if not prompt_id:
            logger.warning("Prompt %s not found; using default.", PROMPT_NAME)
            return DEFAULT_SYSTEM_PROMPT
        resp = client.get_prompt(promptIdentifier=prompt_id)  # DRAFT (latest working copy)
        variants = resp.get("variants") or []
        default_name = resp.get("defaultVariant")
        variant = next(
            (v for v in variants if v.get("name") == default_name),
            variants[0],
        )
        text = variant["templateConfiguration"]["text"]["text"]
        logger.info("Loaded system prompt '%s' from Bedrock Prompt Management.", PROMPT_NAME)
        return text
    except Exception as e:
        logger.warning("Could not load prompt %s (%s); using default.", PROMPT_NAME, e)
        return DEFAULT_SYSTEM_PROMPT


def _resolve_guardrail_id(client, name: str) -> str | None:
    """Find a guardrail's id by its stable name (paginating ListGuardrails)."""
    token = None
    while True:
        resp = client.list_guardrails(**({"nextToken": token} if token else {}))
        for summary in resp.get("guardrails", []):
            if summary.get("name") == name:
                return summary.get("id")
        token = resp.get("nextToken")
        if not token:
            return None


def build_model() -> BedrockModel:
    """Build the BedrockModel, attaching the externalized guardrail if resolvable (ADR-0008).

    Fail-open for the MVP: if the guardrail can't be resolved the model still runs. The
    deterministic safety floor (IAM, hardcoded checks, HITL) does not depend on this.
    """
    kwargs = {"model_id": MODEL_ID} if MODEL_ID else {}
    if GUARDRAIL_NAME:
        try:
            client = boto3.client("bedrock", region_name=AWS_REGION)
            guardrail_id = _resolve_guardrail_id(client, GUARDRAIL_NAME)
            if guardrail_id:
                kwargs["guardrail_id"] = guardrail_id
                kwargs["guardrail_version"] = "DRAFT"  # latest working copy
                logger.info("Attached guardrail '%s' to the model.", GUARDRAIL_NAME)
            else:
                logger.warning("Guardrail %s not found; running without it.", GUARDRAIL_NAME)
        except Exception as e:
            logger.warning(
                "Could not resolve guardrail %s (%s); running without it.", GUARDRAIL_NAME, e
            )
    return BedrockModel(**kwargs)


app = BedrockAgentCoreApp()
_model = build_model()
SYSTEM_PROMPT = load_system_prompt()  # fetched once per cold start


@app.entrypoint
def invoke(payload: dict) -> dict:
    user_message = payload.get("prompt", "Say hello and confirm you are running.")
    agent = Agent(model=_model, system_prompt=SYSTEM_PROMPT)
    result = agent(user_message)
    return {"result": str(result)}


if __name__ == "__main__":
    app.run()
