data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

locals {
  ssm_param_prefix = trimprefix(var.param_prefix, "/")
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
  name               = "${var.prefix}-dns-cleanup-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "${var.prefix}-dns-cleanup-lambda-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Scan"
        ]
        Resource = var.main_table_arn
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Resource = [
          "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${local.ssm_param_prefix}/dns/*",
          "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${local.ssm_param_prefix}/secrets/*"
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

resource "aws_iam_role_policy" "lambda_route53_policy" {
  name = "${var.prefix}-dns-cleanup-lambda-route53-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "route53:ListResourceRecordSets",
          "route53:ChangeResourceRecordSets"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_lambda_function" "dns_cleanup" {
  function_name = "${var.prefix}_dns_cleanup"
  package_type  = "Image"
  image_uri     = var.image_uri
  role          = aws_iam_role.lambda_role.arn
  timeout       = 60
  memory_size   = 256

  environment {
    variables = {
      MAIN_TABLE   = var.main_table_name
      PARAM_PREFIX = var.param_prefix
    }
  }

  image_config {
    command = ["dns_cleanup.cleanup_dns"]
  }
}

resource "aws_cloudwatch_log_group" "dns_cleanup" {
  name              = "/aws/lambda/${aws_lambda_function.dns_cleanup.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_event_rule" "on_shutdown" {
  name          = "${var.prefix}-ec2-shutting-down-dns-cleanup"
  event_pattern = <<EOF
{
  "source": ["aws.ec2"],
  "detail-type": ["EC2 Instance State-change Notification"],
  "detail": { "state": ["shutting-down"] }
}
EOF
}

resource "aws_cloudwatch_event_target" "to_dns_cleanup" {
  rule      = aws_cloudwatch_event_rule.on_shutdown.name
  target_id = "DNSCleanup"
  arn       = aws_lambda_function.dns_cleanup.arn
}

resource "aws_lambda_permission" "allow_shutdown" {
  statement_id  = "AllowEC2InvokeDNSCleanup"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dns_cleanup.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.on_shutdown.arn
}
