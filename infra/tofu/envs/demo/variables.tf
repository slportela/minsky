variable "region" {
  type    = string
  default = "us-east-1"
}

variable "bedrock_model_arns" {
  type = list(string)
}

variable "lake_bucket" {
  type = string
}

variable "allowed_cidrs" {
  type    = list(string)
  default = ["0.0.0.0/0"]
}

variable "enable_cloudfront" {
  description = "Use CloudFront with private EC2; no purchased domain required"
  type        = bool
  default     = false
}
