variable "param_prefix" {
  description = "SSM Parameter Store path prefix"
  type        = string
  default     = "/devbox"
}

variable "launch_template_ids" {
  description = "List of EC2 Launch Template IDs to store in SSM"
  type        = list(string)
}

variable "snapshot_table_name" {
  description = "Name of the DynamoDB table for snapshots to store in SSM"
  type        = string
}
