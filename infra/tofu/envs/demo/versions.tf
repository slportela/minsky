terraform {
  required_version = ">= 1.8"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.66.0"
    }
  }

  # Local state is fine for a single-operator demo. For a team, use S3 state with locking:
  # backend "s3" { bucket = "<state-bucket>", key = "minsky/demo.tfstate", region = "us-east-1", use_lockfile = true }
}
