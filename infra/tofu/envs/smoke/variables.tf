variable "region" {
  type    = string
  default = "us-east-1"
}
variable "availability_zone" {
  type    = string
  default = "us-east-1a"
}
variable "bundle_id" {
  description = "Confirm the 2 GB / 60 GB IPv4 bundle with get-bundles before apply."
  type        = string
  default     = "small_3_0"
}
variable "blueprint_id" {
  type    = string
  default = "ubuntu_22_04"
}
variable "ssh_cidrs" {
  description = "Operator IPv4 CIDRs only; do not expose SSH to the entire internet."
  type        = set(string)
  validation {
    condition     = length(var.ssh_cidrs) > 0 && alltrue([for cidr in var.ssh_cidrs : can(cidrnetmask(cidr)) && cidr != "0.0.0.0/0"])
    error_message = "Supply restricted operator IPv4 CIDRs."
  }
}
