terraform {
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

locals {
  dns_enabled    = var.dns_provider != "none"
  use_cloudflare = var.dns_provider == "cloudflare"
  use_route53    = var.dns_provider == "route53"
}

data "cloudflare_zone" "selected" {
  count = local.use_cloudflare ? 1 : 0
  name  = var.dns_zone
}

locals {
  cloudflare_zone_id = local.use_cloudflare ? data.cloudflare_zone.selected[0].id : null
  route53_zone_name  = trim(var.dns_zone, ".")
}

data "aws_route53_zone" "selected" {
  count = local.use_route53 ? 1 : 0
  name  = local.route53_zone_name
}

resource "aws_ssm_parameter" "launch_template" {
  count       = length(var.launch_template_ids)
  name        = "${var.param_prefix}/launchTemplateId_az${count.index}"
  description = "Launch Template ID for Devbox instances in AZ ${count.index}"
  type        = "String"
  value       = var.launch_template_ids[count.index]
  overwrite   = true
}

resource "aws_ssm_parameter" "launch_template_list" {
  name        = "${var.param_prefix}/launchTemplateIds"
  description = "JSON list of all Launch Template IDs for Devbox instances"
  type        = "String"
  value       = jsonencode(var.launch_template_ids)
  overwrite   = true
}

resource "aws_ssm_parameter" "snapshot_table" {
  name        = "${var.param_prefix}/snapshotTable"
  description = "DynamoDB table name for persistent-home snapshots"
  type        = "String"
  value       = var.snapshot_table_name
  overwrite   = true
}

resource "aws_ssm_parameter" "dns_provider" {
  name        = "${var.param_prefix}/dns/provider"
  description = "DNS provider configured for devbox instances"
  type        = "String"
  value       = var.dns_provider
  overwrite   = true
}

resource "aws_ssm_parameter" "dns_zone" {
  count       = local.dns_enabled ? 1 : 0
  name        = "${var.param_prefix}/dns/zone"
  description = "DNS zone used for devbox instance CNAME records"
  type        = "String"
  value       = var.dns_zone
  overwrite   = true

  lifecycle {
    precondition {
      condition     = length(trimspace(var.dns_zone)) > 0
      error_message = "dns_zone must be set when dns_provider is not \"none\"."
    }
  }
}

resource "aws_ssm_parameter" "cloudflare_api_token" {
  count       = local.use_cloudflare ? 1 : 0
  name        = "${var.param_prefix}/secrets/cloudflare/apiToken"
  description = "Cloudflare API token for devbox DNS management"
  type        = "SecureString"
  value       = var.cloudflare_api_token
  overwrite   = true

  lifecycle {
    precondition {
      condition     = length(trimspace(var.cloudflare_api_token)) > 0
      error_message = "cloudflare_api_token must be set when dns_provider is \"cloudflare\"."
    }
  }
}

resource "aws_ssm_parameter" "cloudflare_zone_id" {
  count       = local.use_cloudflare ? 1 : 0
  name        = "${var.param_prefix}/secrets/cloudflare/zoneId"
  description = "Cloudflare zone ID for devbox DNS management"
  type        = "SecureString"
  value       = local.cloudflare_zone_id
  overwrite   = true
}

resource "aws_ssm_parameter" "route53_zone_id" {
  count       = local.use_route53 ? 1 : 0
  name        = "${var.param_prefix}/dns/route53/zoneId"
  description = "Route53 hosted zone ID for devbox DNS management"
  type        = "String"
  value       = data.aws_route53_zone.selected[0].zone_id
  overwrite   = true
}
