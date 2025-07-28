variable "prefix" {
  type    = string
  default = "devbox"
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR block for the VPC"
  default     = "10.219.0.0/16"
  validation {
    condition     = tonumber(split("/", var.vpc_cidr)[1]) <= 20
    error_message = "var.vpc_cidr must have prefix length /20 or less (e.g. /16, /20) to allow carving out /24 subnets."
  }
}

variable "ssh_cidr_blocks" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDR blocks allowed to SSH into the instances"
}


