# AWS-managed Lambda base image (includes the Lambda Runtime Interface Client). ARM64 to match
# the AgentCore runtimes (ADR-0005) and this Lambda tier's packaging decision (ADR-0014).
FROM public.ecr.aws/lambda/python:3.13

COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY garden_handler.py ${LAMBDA_TASK_ROOT}/

CMD ["garden_handler.handler"]
