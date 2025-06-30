variable "name" {
  description = "Prefix for all VPC resources"
  type        = string
}
variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
}
variable "ssh_cidr_blocks" {
  description = "List of CIDR blocks allowed SSH"
  type        = list(string)
}
