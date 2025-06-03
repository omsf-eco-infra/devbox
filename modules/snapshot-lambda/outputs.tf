output "lambda_function_arn" {
  description = "ARN of the snapshot Lambda function"
  value       = aws_lambda_function.snapshot.arn
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB table used for snapshots"
  value       = aws_dynamodb_table.snapshots.name
}
