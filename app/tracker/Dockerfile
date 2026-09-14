# AWS-managed Lambda base image (includes the Lambda Runtime Interface Client). A plain
# boto3-only Lambda (no strands-agents, no agent loop) — deterministic query + event-emit.
FROM public.ecr.aws/lambda/python:3.13

COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY tracker.py ${LAMBDA_TASK_ROOT}/

CMD ["tracker.handler"]
