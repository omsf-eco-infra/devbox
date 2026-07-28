output "launch_template_ids" {
  description = "IDs of the Devbox launch templates"
  value       = aws_launch_template.base[*].id
}

output "launch_template_names" {
  description = "Names of the Devbox launch templates"
  value       = aws_launch_template.base[*].name
}

output "ec2_role_arn" {
  description = "ARN of the IAM role passed to launched DevBox instances"
  value       = aws_iam_role.ec2_role.arn
}
