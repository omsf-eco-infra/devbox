variable "prefix" {
  description = "Prefix for all resources"
  type        = string
  default     = "devbox"
}

variable "image_uri" {
  description = "Container image URI for Lambda functions"
  type        = string
}

variable "main_table_name" {
  description = "Name of the DynamoDB table containing project metadata"
  type        = string
}

variable "main_table_arn" {
  description = "ARN of the DynamoDB table containing project metadata"
  type        = string
}

variable "param_prefix" {
  description = "SSM parameter prefix"
  type        = string
  default     = "/devbox"
}

variable "dns_provider" {
  description = "DNS provider used by devbox instances"
  type        = string
  default     = "none"

  validation {
    condition     = contains(["none", "cloudflare", "route53"], var.dns_provider)
    error_message = "dns_provider must be one of: none, cloudflare, route53."
  }
}
