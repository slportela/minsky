mock_provider "aws" {}
variables { ssh_cidrs = ["192.0.2.1/32"] }
run "remote_smoke" {
  command = plan
  assert {
    condition     = aws_lightsail_instance.smoke.bundle_id == "small_3_0" && aws_lightsail_distribution.smoke.bundle_id == "small_1_0"
    error_message = "The smoke must retain the selected small VM and CDN bundles."
  }
  assert {
    condition     = alltrue([for p in aws_lightsail_instance_public_ports.smoke.port_info : contains([22, 80], p.from_port) && p.to_port == p.from_port && (p.from_port != 22 || !contains(p.cidrs, "0.0.0.0/0"))])
    error_message = "Only HTTP and restricted SSH may be published."
  }
  assert {
    condition     = aws_lightsail_distribution.smoke.default_cache_behavior[0].behavior == "dont-cache" && aws_lightsail_distribution.smoke.cache_behavior_settings[0].maximum_ttl == 0 && strcontains(aws_lightsail_distribution.smoke.cache_behavior_settings[0].allowed_http_methods, "POST") && contains(aws_lightsail_distribution.smoke.cache_behavior_settings[0].forwarded_headers[0].headers_allow_list, "Authorization")
    error_message = "Customer requests must support POST, Authorization and disabled caching."
  }
}
run "reject_public_ssh" {
  command = plan
  variables { ssh_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.ssh_cidrs]
}
