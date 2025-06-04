output "create_snapshots_lambda_function" {
  description = "ARN of the snapshot Lambda function"
  value       = aws_lambda_function.create_snapshots.arn
}

output "mark_ready_lambda_function" {
  description = "ARN of the mark ready Lambda function"
  value       = aws_lambda_function.mark_ready.arn
}

output "delete_volume_lambda_function" {
  description = "ARN of the delete volume Lambda function"
  value       = aws_lambda_function.delete_volume.arn
}

output "create_image_lambda_function" {
  description = "ARN of the create image Lambda function"
  value       = aws_lambda_function.create_image.arn
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB table used for snapshots"
  value       = aws_dynamodb_table.main.name
}

output "dynamodb_metadata_name" {
  description = "Name of the DynamoDB table used for metadata"
  value       = aws_dynamodb_table.meta.name
}
