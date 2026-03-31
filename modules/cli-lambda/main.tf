data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

locals {
  repo_root         = abspath("${path.module}/../..")
  lambda_dockerfile = "${local.repo_root}/lambdas/cli_lambda.Dockerfile"
  lambda_source_files = concat(
    ["README.md", "pyproject.toml", "lambdas/cli_lambda.Dockerfile"],
    tolist(fileset(local.repo_root, "src/**")),
  )
  lambda_source_hash = sha256(join("", [
    for relpath in sort(local.lambda_source_files) : filesha256("${local.repo_root}/${relpath}")
  ]))
  # Keep one statement per CLI action so later phases can add permissions incrementally.
  command_policy_statements = [
    {
      sid = "StatusEc2Read"
      # The phase 1 `status` path reuses DevBoxManager.list_* inventory helpers,
      # which currently issue EC2 Describe* calls only. It does not read SSM or
      # DynamoDB as part of the status action itself.
      actions = [
        "ec2:DescribeImages",
        "ec2:DescribeInstances",
        "ec2:DescribeSnapshots",
        "ec2:DescribeVolumes"
      ]
      resources = ["*"]
    }
  ]
}

resource "aws_ecr_repository" "cli" {
  name = "${var.prefix}-cli-lambda-repo"
}

resource "null_resource" "build_and_push" {
  triggers = {
    source_hash = local.lambda_source_hash
  }

  provisioner "local-exec" {
    command = <<EOT
aws ecr get-login-password --region ${data.aws_region.current.name} \
  | docker login --username AWS --password-stdin ${aws_ecr_repository.cli.repository_url}
docker build --platform linux/amd64 -f ${local.lambda_dockerfile} -t cli-lambda ${local.repo_root}
docker tag cli-lambda:latest ${aws_ecr_repository.cli.repository_url}:latest
docker push ${aws_ecr_repository.cli.repository_url}:latest
EOT
  }

  depends_on = [aws_ecr_repository.cli]
}

data "aws_ecr_image" "latest" {
  repository_name = aws_ecr_repository.cli.name
  image_tag       = "latest"
  depends_on      = [null_resource.build_and_push]
}

locals {
  image_uri = "${aws_ecr_repository.cli.repository_url}@${data.aws_ecr_image.latest.image_digest}"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "lambda_role" {
  name               = "${var.prefix}-cli-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "${var.prefix}-cli-lambda-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        for statement in local.command_policy_statements : {
          Sid      = statement.sid
          Effect   = "Allow"
          Action   = statement.actions
          Resource = statement.resources
        }
      ],
      [
        {
          Sid    = "LambdaLogging"
          Effect = "Allow"
          Action = [
            "logs:CreateLogGroup",
            "logs:CreateLogStream",
            "logs:PutLogEvents"
          ]
          Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/*"
        }
      ]
    )
  })
}

resource "aws_lambda_function" "cli" {
  function_name = "${var.prefix}_cli"
  package_type  = "Image"
  image_uri     = local.image_uri
  role          = aws_iam_role.lambda_role.arn
  timeout       = 30
  memory_size   = 256

  environment {
    variables = {
      AWS_LWA_INVOKE_MODE           = "response_stream"
      AWS_LWA_READINESS_CHECK_PATH  = "/healthz"
      PORT                          = "8080"
    }
  }

  depends_on = [null_resource.build_and_push]
}

resource "aws_cloudwatch_log_group" "cli" {
  name              = "/aws/lambda/${aws_lambda_function.cli.function_name}"
  retention_in_days = 14
}

resource "aws_lambda_function_url" "cli" {
  function_name      = aws_lambda_function.cli.function_name
  authorization_type = "AWS_IAM"
  invoke_mode        = "RESPONSE_STREAM"
}

resource "aws_ssm_parameter" "function_url" {
  name      = "${var.param_prefix}/cli/functionUrl"
  type      = "String"
  value     = aws_lambda_function_url.cli.function_url
  overwrite = true
}
