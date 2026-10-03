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
