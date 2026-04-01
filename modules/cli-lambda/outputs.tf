output "function_name" {
  description = "Name of the CLI Lambda function"
  value       = aws_lambda_function.cli.function_name
}

output "function_url" {
  description = "Function URL for the CLI Lambda"
  value       = aws_lambda_function_url.cli.function_url
}

output "function_url_parameter_name" {
  description = "SSM parameter name that stores the CLI Function URL"
  value       = aws_ssm_parameter.function_url.name
}
