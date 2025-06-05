output "param_launch_template" {
  description = "SSM parameter name for the Devbox Launch Template ID"
  value       = aws_ssm_parameter.launch_template.name
}

output "param_snapshot_table" {
  description = "SSM parameter name for the snapshot DynamoDB table"
  value       = aws_ssm_parameter.snapshot_table.name
}
