resource "aws_ssm_parameter" "launch_template" {
  count       = length(var.launch_template_ids)
  name        = "${var.param_prefix}/launchTemplateId_az${count.index}"
  description = "Launch Template ID for Devbox instances in AZ ${count.index}"
  type        = "String"
  value       = var.launch_template_ids[count.index]
  overwrite   = true
}

resource "aws_ssm_parameter" "launch_template_list" {
  name        = "${var.param_prefix}/launchTemplateIds"
  description = "JSON list of all Launch Template IDs for Devbox instances"
  type        = "String"
  value       = jsonencode(var.launch_template_ids)
  overwrite   = true
}

resource "aws_ssm_parameter" "snapshot_table" {
  name        = "${var.param_prefix}/snapshotTable"
  description = "DynamoDB table name for persistent-home snapshots"
  type        = "String"
  value       = var.snapshot_table_name
  overwrite   = true
}
