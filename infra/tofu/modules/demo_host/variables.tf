variable "name" {
  description = "Prefix for every resource name"
  type        = string
}

variable "instance_type" {
  description = "Must fit the whole compose stack"
  type        = string
  default     = "t3.large"
}

variable "disk_gb" {
  type    = number
  default = 60
}

variable "allowed_cidrs" {
  description = "Who can reach the demo (narrow it to the judges' and team's IPs when possible)"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "bedrock_model_arns" {
  description = "Foundation-model or inference-profile ARNs the app may invoke (least privilege: no wildcards)"
  type        = list(string)
}

variable "lake_bucket" {
  description = "Our S3 bucket holding bronze/ and silver/"
  type        = string
}
