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
