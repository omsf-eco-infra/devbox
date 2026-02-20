terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 4.0"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

provider "cloudflare" {
  # Keep Cloudflare optional for module consumers: when not using Cloudflare DNS,
  # configure a placeholder token so provider initialization does not require secrets.
  api_token = var.dns_provider == "cloudflare" ? var.cloudflare_api_token : "0000000000000000000000000000000000000000"
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

module "dns_cleanup_lambda" {
  source = "./modules/dns-cleanup-lambda"

  prefix          = var.prefix
  image_uri       = module.snapshot_lambda.image_uri
  main_table_name = module.snapshot_lambda.dynamodb_table_name
  main_table_arn  = module.snapshot_lambda.dynamodb_table_arn
  param_prefix    = "/${var.prefix}"
}

module "config" {
  source = "./modules/config"

  providers = {
    cloudflare = cloudflare
  }

  param_prefix         = "/${var.prefix}"
  launch_template_ids  = module.devbox.launch_template_ids
  snapshot_table_name  = module.snapshot_lambda.dynamodb_table_name
  dns_provider         = var.dns_provider
  dns_zone             = var.dns_zone
  cloudflare_api_token = var.cloudflare_api_token
}
