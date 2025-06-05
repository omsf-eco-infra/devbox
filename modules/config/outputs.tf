output "param_launch_templates" {
  description = "SSM parameter names for the Devbox Launch Template IDs (one per AZ)"
  value       = aws_ssm_parameter.launch_template[*].name
}

output "param_launch_template_list" {
  description = "SSM parameter name for the JSON list of all Devbox Launch Template IDs"
  value       = aws_ssm_parameter.launch_template_list.name
}

output "param_snapshot_table" {
  description = "SSM parameter name for the snapshot DynamoDB table"
  value       = aws_ssm_parameter.snapshot_table.name
}
