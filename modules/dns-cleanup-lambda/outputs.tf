output "dns_cleanup_lambda_function" {
  description = "ARN of the DNS cleanup Lambda function"
  value       = aws_lambda_function.dns_cleanup.arn
}
