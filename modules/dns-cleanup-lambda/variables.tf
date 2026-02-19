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

variable "param_prefix" {
  description = "SSM parameter prefix"
  type        = string
  default     = "/devbox"
}
