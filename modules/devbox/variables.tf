variable "subnet_ids" {
  description = "List of VPC subnet IDs for EC2 instances across multiple AZs"
  type        = list(string)
}

variable "ssh_sg_ids" {
  description = "Security Group IDs for SSH access"
  type        = list(string)
}

variable "prefix" {
  description = "Prefix for resource names"
  type        = string
  default     = "devbox"
}
