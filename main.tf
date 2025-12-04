terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 4.0"
    }
  }
}

module "vpc" {
  source          = "./modules/vpc"
  name            = var.prefix
  vpc_cidr        = var.vpc_cidr
  ssh_cidr_blocks = var.ssh_cidr_blocks
}

module "devbox" {
  source     = "./modules/devbox"
  subnet_ids = module.vpc.subnet_ids
  ssh_sg_ids = [module.vpc.ssh_sg_id]
  prefix     = var.prefix
}

module "snapshot_lambda" {
  source = "./modules/snapshot-lambda"
  prefix = var.prefix
}

module "config" {
  source              = "./modules/config"
  param_prefix        = "/${var.prefix}"
  launch_template_ids = module.devbox.launch_template_ids
  snapshot_table_name = module.snapshot_lambda.dynamodb_table_name
}
