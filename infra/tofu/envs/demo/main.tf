provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "minsky", Environment = "demo", ManagedBy = "opentofu" }
  }
}

module "demo_host" {
  source             = "../../modules/demo_host"
  name               = "minsky"
  enable_cloudfront  = var.enable_cloudfront
  bedrock_model_arns = var.bedrock_model_arns
  lake_bucket        = var.lake_bucket
  allowed_cidrs      = var.allowed_cidrs
}

output "public_ip" {
  value = module.demo_host.public_ip
}

output "instance_id" {
  value = module.demo_host.instance_id
}

output "demo_url" {
  value = module.demo_host.demo_url
}
