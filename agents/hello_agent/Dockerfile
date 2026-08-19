# ARM64 image for Bedrock AgentCore. Build with:
#   docker buildx build --platform linux/arm64 -t tendril-hello-agent .
FROM public.ecr.aws/docker/library/python:3.13-slim
WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Build-time args (provided by CDK/CLI) — non-secret.
ARG MODEL_ID=""
ARG AWS_REGION=ap-south-1
ARG LOG_LEVEL=INFO

ENV MODEL_ID=$MODEL_ID \
    AWS_REGION=$AWS_REGION \
    AWS_DEFAULT_REGION=$AWS_REGION \
    LOG_LEVEL=$LOG_LEVEL \
    PYTHONUNBUFFERED=1 \
    OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true

COPY --chown=1000:1000 . .

RUN useradd -m -u 1000 bedrock_agentcore || true
USER bedrock_agentcore

EXPOSE 8080
CMD ["opentelemetry-instrument", "python", "-m", "agent"]
