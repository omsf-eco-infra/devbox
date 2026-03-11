# DynamoDB Tables

resource "aws_dynamodb_table" "main" {
  name         = "${var.prefix}-main"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "project"

  attribute {
    name = "project"
    type = "S"
  }

  tags = {
    Name = "${var.prefix}-main"
  }
}

resource "aws_dynamodb_table" "meta" {
  name         = "${var.prefix}-meta"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "project"
  range_key    = "volumeId"

  attribute {
    name = "project"
    type = "S"
  }
  attribute {
    name = "volumeId"
    type = "S"
  }
  attribute {
    name = "snapshotId"
    type = "S"
  }

  global_secondary_index {
    name            = "SnapshotIndex"
    hash_key        = "snapshotId"
    projection_type = "ALL"
  }

  tags = {
    Name = "${var.prefix}-meta"
  }
}

# Containers

resource "aws_ecr_repository" "snapshot_lambda" {
  name = "${var.prefix}-lambda-repo"
}

data "aws_region" "current" {}

locals {
  # Build from repo root so lambdas/Dockerfile can COPY pyproject.toml and src/.
  repo_root         = abspath("${path.module}/../..")
  lambda_dockerfile = "${local.repo_root}/lambdas/Dockerfile"
}


resource "null_resource" "build_and_push" {
  provisioner "local-exec" {
    command = <<EOT
aws ecr get-login-password --region ${data.aws_region.current.name} \
  | docker login --username AWS --password-stdin ${aws_ecr_repository.snapshot_lambda.repository_url}
docker build --platform linux/amd64 -f ${local.lambda_dockerfile} -t snapshot-lambda ${local.repo_root}
docker tag snapshot-lambda:latest ${aws_ecr_repository.snapshot_lambda.repository_url}:latest
docker push ${aws_ecr_repository.snapshot_lambda.repository_url}:latest
EOT
  }
  depends_on = [aws_ecr_repository.snapshot_lambda]
}


data "aws_ecr_image" "latest" {
  repository_name = aws_ecr_repository.snapshot_lambda.name
  image_tag       = "latest"
  depends_on      = [null_resource.build_and_push]
}


locals {
  image_uri = "${aws_ecr_repository.snapshot_lambda.repository_url}@${data.aws_ecr_image.latest.image_digest}"
}

# IAM

data "aws_caller_identity" "current" {}

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
  name               = "${var.prefix}-snapshot-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "${var.prefix}-snapshot-lambda-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeVolumes",
          "ec2:CreateSnapshot",
          "ec2:DeleteSnapshot",
          "ec2:CreateTags",
          "ec2:RegisterImage",
          "ec2:DeregisterImage",
          "ec2:DeleteVolume",
          "ec2:DescribeSnapshots",
          "ec2:DescribeImages"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = [
          aws_dynamodb_table.main.arn,
          aws_dynamodb_table.meta.arn,
          "${aws_dynamodb_table.meta.arn}/index/SnapshotIndex"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/*"
      }
    ]
  })
}

# CloudWatch

resource "aws_cloudwatch_log_group" "create_snapshots" {
  name              = "/aws/lambda/${aws_lambda_function.create_snapshots.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "create_image" {
  name              = "/aws/lambda/${aws_lambda_function.create_image.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "mark_ready" {
  name              = "/aws/lambda/${aws_lambda_function.mark_ready.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "delete_volume" {
  name              = "/aws/lambda/${aws_lambda_function.delete_volume.function_name}"
  retention_in_days = 14
}

# Lambdas


resource "aws_lambda_function" "create_snapshots" {
  function_name = "${var.prefix}_create_snapshots"
  package_type  = "Image"
  image_uri     = local.image_uri
  role          = aws_iam_role.lambda_role.arn
  timeout       = 300
  memory_size   = 256

  environment {
    variables = {
      MAIN_TABLE = aws_dynamodb_table.main.name
      META_TABLE = aws_dynamodb_table.meta.name
    }
  }

  image_config {
    # This tells the runtime to use the create_snapshots() function in snapshot_lambda.py
    command = ["snapshot_lambda.create_snapshots"]
  }

  depends_on = [
    null_resource.build_and_push,
  ]
}

resource "aws_lambda_function" "create_image" {
  function_name = "${var.prefix}_create_image"
  package_type  = "Image"
  image_uri     = local.image_uri
  role          = aws_iam_role.lambda_role.arn
  timeout       = 300
  memory_size   = 256

  environment {
    variables = {
      MAIN_TABLE = aws_dynamodb_table.main.name
      META_TABLE = aws_dynamodb_table.meta.name
    }
  }

  image_config {
    command = ["snapshot_lambda.create_image"]
  }

  depends_on = [
    null_resource.build_and_push,
  ]
}

resource "aws_lambda_function" "mark_ready" {
  function_name = "${var.prefix}_mark_ready"
  package_type  = "Image"
  image_uri     = local.image_uri
  role          = aws_iam_role.lambda_role.arn
  timeout       = 60

  environment {
    variables = {
      MAIN_TABLE = aws_dynamodb_table.main.name
      META_TABLE = aws_dynamodb_table.meta.name
    }
  }

  image_config {
    command = ["snapshot_lambda.mark_ready"]
  }

  depends_on = [
    null_resource.build_and_push,
  ]
}

resource "aws_lambda_function" "delete_volume" {
  function_name = "${var.prefix}_delete_volume"
  package_type  = "Image"
  image_uri     = local.image_uri
  role          = aws_iam_role.lambda_role.arn
  timeout       = 60

  environment {
    variables = {
      MAIN_TABLE = aws_dynamodb_table.main.name
      META_TABLE = aws_dynamodb_table.meta.name
    }
  }

  image_config {
    command = ["snapshot_lambda.delete_volume"]
  }

  depends_on = [
    null_resource.build_and_push,
  ]
}

# CloudWatch Event Rules and Targets

resource "aws_cloudwatch_event_rule" "on_shutdown" {
  name          = "${var.prefix}-ec2-shutting-down"
  event_pattern = <<EOF
{
  "source": ["aws.ec2"],
  "detail-type": ["EC2 Instance State-change Notification"],
  "detail": { "state": ["shutting-down"] }
}
EOF
}

resource "aws_cloudwatch_event_target" "to_create_snapshots" {
  rule      = aws_cloudwatch_event_rule.on_shutdown.name
  target_id = "CreateSnapshots"
  arn       = aws_lambda_function.create_snapshots.arn
}

resource "aws_lambda_permission" "allow_shutdown" {
  statement_id  = "AllowEC2InvokeCR"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.create_snapshots.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.on_shutdown.arn
}

resource "aws_cloudwatch_event_rule" "on_snapshot_complete" {
  name          = "${var.prefix}-snapshot-complete"
  event_pattern = <<EOF
{
  "source": ["aws.ec2"],
  "detail-type": ["EBS Snapshot Notification"],
  "detail": { "result": ["succeeded"] }
}
EOF
}

resource "aws_cloudwatch_event_target" "to_create_image" {
  rule      = aws_cloudwatch_event_rule.on_snapshot_complete.name
  target_id = "CreateImage"
  arn       = aws_lambda_function.create_image.arn
}

resource "aws_lambda_permission" "allow_snapshot_complete" {
  statement_id  = "AllowSnapshotInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.create_image.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.on_snapshot_complete.arn
}

resource "aws_cloudwatch_event_rule" "on_ami_available" {
  name          = "${var.prefix}-ami-available"
  event_pattern = <<EOF
{
  "source": ["aws.ec2"],
  "detail-type": ["EC2 AMI State Change"],
  "detail": { "State": ["available"] }
}
EOF
}

resource "aws_cloudwatch_event_target" "to_mark_ready" {
  rule      = aws_cloudwatch_event_rule.on_ami_available.name
  target_id = "MarkReady"
  arn       = aws_lambda_function.mark_ready.arn
}

resource "aws_lambda_permission" "allow_ami_available" {
  statement_id  = "AllowAMIAvailableInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.mark_ready.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.on_ami_available.arn
}

resource "aws_cloudwatch_event_rule" "on_volume_available" {
  name          = "${var.prefix}-volume-available"
  event_pattern = <<EOF
{
  "source": ["aws.ec2"],
  "detail-type": ["EBS Volume State-change Notification"],
  "detail": { "state": ["available"] }
}
EOF
}

resource "aws_cloudwatch_event_target" "to_delete_volume" {
  rule      = aws_cloudwatch_event_rule.on_volume_available.name
  target_id = "DeleteVolume"
  arn       = aws_lambda_function.delete_volume.arn
}

resource "aws_lambda_permission" "allow_volume_available" {
  statement_id  = "AllowVolumeAvailableInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.delete_volume.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.on_volume_available.arn
}
