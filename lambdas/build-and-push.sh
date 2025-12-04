if [ -z "$AWS_REGION" ]; then
    AWS_REGION="us-east-1"
fi

if [ -z "$REPO_NAME" ]; then
    REPO_NAME="devbox-lambda-repo"
fi

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS \
  --password-stdin ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com
docker build --platform linux/amd64 -t $REPO_NAME .
docker tag ${REPO_NAME}:latest \
  ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO_NAME}:latest
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO_NAME}:latest
