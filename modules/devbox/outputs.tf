output "launch_template_id" {
  description = "ID of the Devbox launch template"
  value       = aws_launch_template.base.id
}
