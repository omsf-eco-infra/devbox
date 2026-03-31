FROM public.ecr.aws/docker/library/python:3.13-slim

COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 \
  /lambda-adapter /opt/extensions/lambda-adapter

ENV PYTHONUNBUFFERED=1
WORKDIR /var/task

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[cli_lambda]"

EXPOSE 8080

CMD ["uvicorn", "devbox.cli_lambda.app:app", "--host", "0.0.0.0", "--port", "8080"]
