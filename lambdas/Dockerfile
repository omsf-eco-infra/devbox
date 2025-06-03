FROM public.ecr.aws/lambda/python:3.13

# Copy function code
COPY snapshot_lambda.py ${LAMBDA_TASK_ROOT}

# (No extra dependencies; boto3 is provided in the Lambda runtime)

# Set the handler
CMD ["snapshot_lambda.lambda_handler"]
