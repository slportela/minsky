mock_provider "aws" {}
variables { ssh_cidrs = ["192.0.2.1/32"] }
run "remote_smoke" {
  command = plan
  assert {
    condition     = aws_lightsail_instance.smoke.bundle_id == "small_3_0" && aws_lightsail_distribution.smoke.bundle_id == "small_1_0"
    error_message = "The smoke must retain the selected small VM and CDN bundles."
  }
  assert {
    condition     = alltrue([for p in aws_lightsail_instance_public_ports.smoke.port_info : contains([22, 80], p.from_port) && p.to_port == p.from_port && (p.from_port != 22 || (p.cidrs == toset(var.ssh_cidrs) && alltrue([for cidr in p.cidrs : can(regex("/32$", cidr))])))])
    error_message = "Only HTTP and restricted SSH may be published."
  }
  assert {
    condition = (
      aws_lightsail_distribution.smoke.origin[0].protocol_policy == "http-only" &&
      aws_lightsail_distribution.smoke.default_cache_behavior[0].behavior == "dont-cache" &&
      aws_lightsail_distribution.smoke.cache_behavior_settings[0].minimum_ttl == 0 &&
      aws_lightsail_distribution.smoke.cache_behavior_settings[0].default_ttl == 1 &&
      aws_lightsail_distribution.smoke.cache_behavior_settings[0].maximum_ttl == 1 &&
      aws_lightsail_distribution.smoke.cache_behavior_settings[0].allowed_http_methods == "GET,HEAD,OPTIONS,PUT,PATCH,POST,DELETE" &&
      aws_lightsail_distribution.smoke.cache_behavior_settings[0].cached_http_methods == "GET,HEAD" &&
      aws_lightsail_distribution.smoke.cache_behavior_settings[0].forwarded_headers[0].option == "allow-list" &&
      contains(aws_lightsail_distribution.smoke.cache_behavior_settings[0].forwarded_headers[0].headers_allow_list, "Authorization") &&
      contains(aws_lightsail_distribution.smoke.cache_behavior_settings[0].forwarded_headers[0].headers_allow_list, "Host") &&
      aws_lightsail_distribution.smoke.cache_behavior_settings[0].forwarded_cookies[0].option == "all" &&
      aws_lightsail_distribution.smoke.cache_behavior_settings[0].forwarded_query_strings[0].option
    )
    error_message = "Keep HTTP origin, no caching, Authorization and Host forwarded, all cookies/queries and all API methods."
  }
}
run "reject_public_ssh" {
  command = plan
  variables { ssh_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.ssh_cidrs]
}

run "reject_public_ssh_pair" {
  command = plan
  variables { ssh_cidrs = ["0.0.0.0/1", "128.0.0.0/1"] }
  expect_failures = [var.ssh_cidrs]
}

run "reject_subnet_ssh" {
  command = plan
  variables { ssh_cidrs = ["192.0.2.0/24"] }
  expect_failures = [var.ssh_cidrs]
}

run "reject_invalid_ssh" {
  command = plan
  variables { ssh_cidrs = ["invalid/32"] }
  expect_failures = [var.ssh_cidrs]
}

run "reject_ipv6_ssh" {
  command = plan
  variables { ssh_cidrs = ["2001:db8::1/32"] }
  expect_failures = [var.ssh_cidrs]
}

run "reject_empty_ssh" {
  command = plan
  variables { ssh_cidrs = [] }
  expect_failures = [var.ssh_cidrs]
}
