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

variable "dns_provider" {
  description = "DNS provider to configure for devbox instances"
  type        = string
  default     = "none"

  validation {
    condition     = contains(["none", "cloudflare", "route53"], var.dns_provider)
    error_message = "dns_provider must be one of: none, cloudflare, route53."
  }
}

variable "dns_zone" {
  description = "DNS zone name used for devbox instance CNAME records"
  type        = string
  default     = ""

}

variable "cloudflare_api_token" {
  description = "Cloudflare API token used for DNS management"
  type        = string
  default     = ""
  sensitive   = true
}
