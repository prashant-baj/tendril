# AWS-managed Lambda base image (includes the Lambda Runtime Interface Client). ARM64 to match
# the rest of the Lambda tier (ADR-0014). Pillow and pillow-heif ship manylinux aarch64 wheels,
# so no compiler toolchain is needed in the image.
FROM public.ecr.aws/lambda/python:3.13

COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY image_tool.py ${LAMBDA_TASK_ROOT}/

CMD ["image_tool.handler"]
