FROM public.ecr.aws/lambda/python:3.13

WORKDIR ${LAMBDA_TASK_ROOT}

# Install the devbox package from the repo source.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Copy Lambda handlers.
COPY lambdas/snapshot_lambda.py ${LAMBDA_TASK_ROOT}
COPY lambdas/dns_cleanup.py ${LAMBDA_TASK_ROOT}

CMD ["snapshot_lambda.create_snapshots"]
