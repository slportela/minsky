mock_provider "aws" {}
run "allow_operator" {
  command = plan
  variables { ssh_cidrs = ["192.0.2.1/32"] }
}
run "reject_public_ssh_pair" {
  command = plan
  variables { ssh_cidrs = ["0.0.0.0/1", "128.0.0.0/1"] }
  expect_failures = [var.ssh_cidrs]
}
