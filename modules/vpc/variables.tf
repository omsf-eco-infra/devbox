variable "name" {
  description = "Prefix for all VPC resources"
  type        = string
}
variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
}
variable "subnet_cidr" {
  description = "CIDR block for the subnet"
  type        = string
}
variable "availability_zone" {
  description = "AZ to place the subnet in"
  type        = string
}
variable "ssh_cidr_blocks" {
  description = "List of CIDR blocks allowed SSH"
  type        = list(string)
}
