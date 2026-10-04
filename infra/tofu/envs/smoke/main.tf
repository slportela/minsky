provider "aws" {
  region = var.region
  default_tags { tags = { Project = "minsky", Environment = "smoke", ManagedBy = "opentofu" } }
}
module "smoke" {
  source            = "../../modules/lightsail_smoke"
  region            = var.region
  availability_zone = var.availability_zone
  ssh_cidrs         = var.ssh_cidrs
  bundle_id         = var.bundle_id
  blueprint_id      = var.blueprint_id
}
output "demo_url" { value = module.smoke.demo_url }
output "public_ip" { value = module.smoke.public_ip }
output "instance_name" { value = module.smoke.instance_name }
