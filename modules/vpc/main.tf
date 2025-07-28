# Get available AZs in the specified region
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  vpc_prefix_len  = tonumber(split("/", var.vpc_cidr)[1])
  subnet_new_bits = 24 - local.vpc_prefix_len
  az_names        = data.aws_availability_zones.available.names
}

resource "aws_vpc" "this" {
  cidr_block = var.vpc_cidr
  tags       = { Name = "${var.name}-vpc" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-igw" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-public-rt" }
}

resource "aws_route" "default_route" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_subnet" "this" {
  for_each          = { for az in local.az_names : az => az }
  vpc_id            = aws_vpc.this.id
  availability_zone = each.value
  cidr_block = cidrsubnet(
    var.vpc_cidr,
    local.subnet_new_bits,
    index(local.az_names, each.value)
  )
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.name}-subnet-${each.value}" }
}

resource "aws_route_table_association" "public_assoc" {
  for_each       = aws_subnet.this
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "ssh" {
  name        = "${var.name}-ssh-sg"
  description = "Allow SSH from trusted IPs"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.ssh_cidr_blocks
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name}-ssh-sg" }
}
