variable "subnet_id" {
  description = "VPC subnet ID for EC2"
  type        = string
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
