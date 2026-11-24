# AWS-managed Lambda base image. ARM64 to match the rest of the Lambda tier (ADR-0014).
# imageio-ffmpeg and Pillow ship manylinux aarch64 wheels (ffmpeg is a bundled static binary),
# so no compiler toolchain or system packages are needed.
FROM public.ecr.aws/lambda/python:3.13

COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY media_tool.py ${LAMBDA_TASK_ROOT}/

CMD ["media_tool.handler"]
