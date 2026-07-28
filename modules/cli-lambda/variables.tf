variable "prefix" {
  description = "Prefix for all resources"
  type        = string
  default     = "devbox"
}

variable "param_prefix" {
  description = "SSM parameter prefix"
  type        = string
  default     = "/devbox"
}

variable "snapshot_table_arn" {
  description = "ARN of the main DevBox snapshot table"
  type        = string
}

variable "ec2_instance_role_arn" {
  description = "ARN of the IAM role attached to launched DevBox instances"
  type        = string
}

variable "dns_provider" {
  description = "DNS provider used for optional launch-time CNAME assignment"
  type        = string
  default     = "none"

  validation {
    condition     = contains(["none", "cloudflare", "route53"], var.dns_provider)
    error_message = "dns_provider must be one of: none, cloudflare, route53."
  }
}
