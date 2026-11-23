# AWS-managed Lambda base image (includes the Lambda Runtime Interface Client), ARM64 like the
# rest of the Lambda tier (ADR-0014).
FROM public.ecr.aws/lambda/python:3.13

COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY ingestion.py ${LAMBDA_TASK_ROOT}/

CMD ["ingestion.handler"]
