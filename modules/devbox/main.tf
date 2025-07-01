resource "aws_iam_role" "ec2_role" {
  name = "${var.prefix}-ec2-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_policy" "ec2_policy" {
  name = "${var.prefix}-ec2-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ec2:AttachVolume",
        "ec2:DetachVolume",
        "ec2:DescribeVolumes",
        "ec2:DescribeInstances"
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ec2_attach" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = aws_iam_policy.ec2_policy.arn
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "${var.prefix}-ec2-profile"
  role = aws_iam_role.ec2_role.name
}

resource "aws_launch_template" "base" {
  count       = length(var.subnet_ids)
  name_prefix = "${var.prefix}-az${count.index}-"

  iam_instance_profile {
    name = aws_iam_instance_profile.ec2_profile.name
  }

  network_interfaces {
    subnet_id                   = var.subnet_ids[count.index]
    security_groups             = var.ssh_sg_ids
    associate_public_ip_address = true
  }

  # seems to duplicate the drive here
  #block_device_mappings {
  #device_name = "/dev/xvda"
  #ebs {
  #volume_size           = 45
  #volume_type           = "gp3"
  #delete_on_termination = false
  #}
  #}

  tags = {
    Name = "${var.prefix}-launch-template-az${count.index}"
  }
}
