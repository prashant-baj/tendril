"""Hello agent — minimal Strands + AgentCore runtime to validate deployment (TF-04).

Model id and config come from environment variables; nothing is hardcoded.
"""
import logging
import os

from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("tendril.hello_agent")

MODEL_ID = os.getenv("MODEL_ID")  # provided per-environment; never hardcoded

app = BedrockAgentCoreApp()
_model = BedrockModel(model_id=MODEL_ID) if MODEL_ID else BedrockModel()

SYSTEM_PROMPT = (
    "You are Tendril's hello agent. Confirm the runtime is alive and, if asked a "
    "gardening question, answer briefly. Keep responses short."
)


@app.entrypoint
def invoke(payload: dict) -> dict:
    user_message = payload.get("prompt", "Say hello and confirm you are running.")
    agent = Agent(model=_model, system_prompt=SYSTEM_PROMPT)
    result = agent(user_message)
    return {"result": str(result)}


if __name__ == "__main__":
    app.run()
