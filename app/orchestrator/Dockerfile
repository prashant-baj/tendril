# AWS-managed Lambda base image (includes the Lambda Runtime Interface Client). ARM64 to match
# the AgentCore runtimes (ADR-0005) and this Lambda tier's packaging decision (ADR-0014) — the
# orchestrator isn't literally a "Client API"/"Tool" Lambda, but needs strands-agents + boto3,
# well past what a zip package handles gracefully, so the same reasoning applies.
FROM public.ecr.aws/lambda/python:3.13

COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY orchestrator.py ${LAMBDA_TASK_ROOT}/

CMD ["orchestrator.handler"]
