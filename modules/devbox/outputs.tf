output "launch_template_ids" {
  description = "IDs of the Devbox launch templates"
  value       = aws_launch_template.base[*].id
}

output "launch_template_names" {
  description = "Names of the Devbox launch templates"
  value       = aws_launch_template.base[*].name
}
