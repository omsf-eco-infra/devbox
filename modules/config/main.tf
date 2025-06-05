resource "aws_ssm_parameter" "launch_template" {
  name        = "${var.param_prefix}/launchTemplateId"
  description = "Launch Template ID for Devbox instances"
  type        = "String"
  value       = var.launch_template_id
  overwrite   = true
}

resource "aws_ssm_parameter" "snapshot_table" {
  name        = "${var.param_prefix}/snapshotTable"
  description = "DynamoDB table name for persistent-home snapshots"
  type        = "String"
  value       = var.snapshot_table_name
  overwrite   = true
}
