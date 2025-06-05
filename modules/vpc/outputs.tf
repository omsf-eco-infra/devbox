output "vpc_id" {
  description = "The ID of the created VPC"
  value       = aws_vpc.this.id
}

output "subnet_id" {
  description = "The ID of the created Subnet"
  value       = aws_subnet.this.id
}

output "ssh_sg_id" {
  description = "The ID of the SSH Security Group"
  value       = aws_security_group.ssh.id
}

output "internet_gateway_id" {
  value       = aws_internet_gateway.this.id
  description = "ID of the Internet Gateway"
}

output "public_route_table_id" {
  value       = aws_route_table.public.id
  description = "ID of the public Route Table"
}
