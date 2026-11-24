# AWS-managed Lambda base image, ARM64 like the orchestrator (ADR-0012, ADR-0014): the Supervisor
# is the second Lambda-hosted Strands agent, event-triggered and short-lived (ADR-0039 §1).
FROM public.ecr.aws/lambda/python:3.13

COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY supervisor.py supervisor_rollup.py ${LAMBDA_TASK_ROOT}/

CMD ["supervisor.handler"]
