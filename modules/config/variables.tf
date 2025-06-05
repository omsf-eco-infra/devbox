variable "param_prefix" {
  description = "SSM Parameter Store path prefix"
  type        = string
  default     = "/devbox"
}

variable "launch_template_id" {
  description = "ID of the EC2 Launch Template to store in SSM"
  type        = string
}

variable "snapshot_table_name" {
  description = "Name of the DynamoDB table for snapshots to store in SSM"
  type        = string
}
