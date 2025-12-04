output "launch_template_ids" {
  value       = module.devbox.launch_template_ids
  description = "List of launch template IDs for each availability zone"
}

output "prefix" {
  value = var.prefix
}
