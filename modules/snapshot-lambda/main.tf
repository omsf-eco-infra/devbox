resource "aws_ecr_repository" "snapshot_lambda" {
  name = "${var.prefix}-snapshot-lambda-repo"
}

data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

resource "null_resource" "build_and_push" {
  provisioner "local-exec" {
    command = <<EOT
aws ecr get-login-password --region ${data.aws_region.current.name} \
  | docker login --username AWS --password-stdin ${aws_ecr_repository.snapshot_lambda.repository_url}
docker build --platform linux/amd64 -t snapshot-lambda ./lambdas
docker tag snapshot-lambda:latest ${aws_ecr_repository.snapshot_lambda.repository_url}:latest
docker push ${aws_ecr_repository.snapshot_lambda.repository_url}:latest
EOT
  }
  depends_on = [aws_ecr_repository.snapshot_lambda]
}

resource "aws_dynamodb_table" "snapshots" {
  name         = "${var.prefix}-home-snapshots"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user"
  range_key    = "project"

  attribute {
    name = "user"
    type = "S"
  }
  attribute {
    name = "project"
    type = "S"
  }

  tags = { Name = "${var.prefix}-home-snapshots" }
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
  name               = "snapshot-lambda-role"
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
          "ec2:DescribeSnapshots",
          "ec2:DeleteSnapshot",
          "ec2:DeleteVolume",
          "ec2:CreateTags",
          "ec2:DetachVolume",
          "ec2:DescribeVolumeStatus",
          "ec2:RegisterImage",
          "ec2:DescribeImages",
          "ec2:DeregisterImage",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem"
        ]
        Resource = aws_dynamodb_table.snapshots.arn
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${aws_lambda_function.snapshot.function_name}:*"
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "snapshot_lambda" {
  name              = "/aws/lambda/${aws_lambda_function.snapshot.function_name}"
  retention_in_days = 14
}

data "aws_ecr_image" "snapshot" {
  repository_name = aws_ecr_repository.snapshot_lambda.name
  image_tag       = "latest"
  # ensure we wait for the repo to exist & the push to finish:
  depends_on = [null_resource.build_and_push]
}


resource "aws_lambda_function" "snapshot" {
  function_name = "${var.prefix}-home-snapshot"
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.snapshot_lambda.repository_url}@${data.aws_ecr_image.snapshot.image_digest}"
  role          = aws_iam_role.lambda_role.arn
  timeout       = 900

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.snapshots.name
    }
  }
  depends_on = [
    null_resource.build_and_push,
  ]
}

resource "aws_cloudwatch_event_rule" "ec2_shutdown" {
  name          = "${var.prefix}-ec2-shutdown"
  event_pattern = <<EOF
{
  "source": ["aws.ec2"],
  "detail-type": ["EC2 Instance State-change Notification"],
  "detail": { "state": ["shutting-down","terminated"] }
}
EOF
}

resource "aws_cloudwatch_event_target" "snapshot_target" {
  rule      = aws_cloudwatch_event_rule.ec2_shutdown.name
  target_id = "SnapshotLambda"
  arn       = aws_lambda_function.snapshot.arn
}

resource "aws_lambda_permission" "allow_event" {
  statement_id  = "AllowCloudWatchInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.snapshot.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ec2_shutdown.arn
}
