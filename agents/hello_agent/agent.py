"""Hello agent — minimal Strands + AgentCore runtime to validate deployment (TF-04).

This is also the **shared agent template** every `agents/registry/*.json` entry currently
points at (ADR-0012's 2026-09-12 refinement) — "hello" and "vision" (the plant-vision
specialist) both run this exact codebase, differing only by their registry config
(model_id/prompt_name/guardrail_name). A registry entry is only its config, not its code.

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
import urllib.request

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


DEFAULT_USER_MESSAGE = "Say hello and confirm you are running."

app = BedrockAgentCoreApp()
_model: BedrockModel | None = None
_system_prompt: str | None = None


def _get_model() -> BedrockModel:
    """Build the model once and reuse it (resolved on first request, not at import)."""
    global _model
    if _model is None:
        _model = build_model()
    return _model


def _get_system_prompt() -> str:
    """Resolve the system prompt once and reuse it (cached after first request)."""
    global _system_prompt
    if _system_prompt is None:
        _system_prompt = load_system_prompt()
    return _system_prompt


def resolve_user_message(payload: dict) -> str:
    """Deterministic input validation (ADR-0008 hardcoded floor).

    Accepts only a dict payload; a missing, non-string, or blank ``prompt`` falls back
    to a safe default rather than passing malformed input to the model.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object with an optional 'prompt' string")
    prompt = payload.get("prompt", DEFAULT_USER_MESSAGE)
    if not isinstance(prompt, str) or not prompt.strip():
        return DEFAULT_USER_MESSAGE
    return prompt


ALLOWED_IMAGE_FORMATS = {"png", "jpeg", "gif", "webp"}  # strands.types.media.ImageContent
IMAGE_FETCH_TIMEOUT_SECONDS = 10
MAX_IMAGE_BYTES = 15 * 1024 * 1024  # comfortably above a phone photo; bounds a bad/huge URL


def fetch_image_bytes(url: str) -> bytes:
    """Specialists get no direct S3 IAM (ADR-0013) — a photo arrives as a short-lived
    presigned GET URL from the orchestrator (the trusted-tier exception) and is fetched over
    plain HTTPS here, the same posture as calling any other Tool API."""
    with urllib.request.urlopen(url, timeout=IMAGE_FETCH_TIMEOUT_SECONDS) as resp:  # noqa: S310
        data = resp.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"image exceeds {MAX_IMAGE_BYTES} bytes")
    return data


def resolve_content(payload: dict) -> str | list[dict]:
    """Builds the Agent() call's input: plain text, or [image, text] content blocks when the
    orchestrator attached a photo (``payload['imageUrl']``/``['imageFormat']``) — e.g. for the
    vision specialist (agents/registry/vision.json). Falls back to text-only if the image
    can't be fetched, rather than failing the whole turn over a bad/expired URL — every
    specialist can receive an image (config-driven, not vision-specific code)."""
    prompt = resolve_user_message(payload)
    image_url = payload.get("imageUrl")
    image_format = payload.get("imageFormat")
    if not image_url or image_format not in ALLOWED_IMAGE_FORMATS:
        return prompt
    try:
        image_bytes = fetch_image_bytes(image_url)
    except Exception as e:
        logger.warning("Could not fetch image %s (%s); continuing text-only.", image_url, e)
        return prompt
    logger.info(
        "Fetched image (%d bytes, format=%s) for this turn.", len(image_bytes), image_format
    )
    return [
        {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
        {"text": prompt},
    ]


@app.entrypoint
def invoke(payload: dict) -> dict:
    content = resolve_content(payload)
    agent = Agent(model=_get_model(), system_prompt=_get_system_prompt())
    result = agent(content)
    text = str(result)
    logger.info("invoke returning %d chars: %s", len(text), text[:500])
    return {"result": text}


if __name__ == "__main__":
    app.run()
