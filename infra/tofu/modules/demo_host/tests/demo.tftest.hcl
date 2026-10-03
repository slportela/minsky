mock_provider "aws" {
  mock_data "aws_ssm_parameter" {
    defaults = { value = "ami-0123456789abcdef0" }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["us-east-1a"] }
  }
  mock_data "aws_vpc" {
    defaults = { id = "vpc-0123456789abcdef0" }
  }
  mock_data "aws_subnets" {
    defaults = { ids = ["subnet-0123456789abcdef0"] }
  }
  mock_resource "aws_instance" {
    defaults = {
      arn         = "arn:aws:ec2:us-east-1:123456789012:instance/i-0123456789abcdef0"
      private_dns = "ip-10-42-1-10.ec2.internal"
    }
  }
}

variables {
  name               = "minsky-test"
  lake_bucket        = "synthetic-test-lake"
  bedrock_model_arns = ["arn:aws:bedrock:us-east-1::foundation-model/example"]
}

run "private_cloudfront" {
  command = plan
  variables { enable_cloudfront = true }
  assert {
    condition     = length(aws_eip.demo) == 0 && !aws_subnet.private[0].map_public_ip_on_launch
    error_message = "The origin must not have a public Elastic IP or auto-assigned public IP."
  }
  assert {
    condition     = alltrue([for rule in aws_security_group.demo.ingress : length(coalesce(rule.cidr_blocks, toset([]))) == 0 && rule.from_port == 80 && length(rule.prefix_list_ids) == 1])
    error_message = "Private demo ingress must only allow CloudFront origin-facing HTTP."
  }
  assert {
    condition     = aws_cloudfront_distribution.demo[0].viewer_certificate[0].cloudfront_default_certificate && aws_cloudfront_distribution.demo[0].default_cache_behavior[0].viewer_protocol_policy == "redirect-to-https"
    error_message = "Browser access must use the AWS hostname certificate and redirect to HTTPS."
  }
  assert {
    condition     = data.aws_cloudfront_cache_policy.disabled[0].name == "Managed-CachingDisabled" && data.aws_cloudfront_origin_request_policy.viewer[0].name == "Managed-AllViewerExceptHostHeader" && contains(aws_cloudfront_distribution.demo[0].default_cache_behavior[0].allowed_methods, "POST")
    error_message = "Chat must bypass caching, preserve authorization and accept POST."
  }
  assert {
    condition     = alltrue([for rule in aws_cloudfront_distribution.demo[0].custom_error_response : rule.error_caching_min_ttl == 0 && rule.response_code == null])
    error_message = "Errors must not be cached or rewritten as successes."
  }
}

run "legacy_direct_demo" {
  command = plan
  variables { enable_cloudfront = false }
  assert {
    condition     = length(aws_cloudfront_distribution.demo) == 0 && length(aws_nat_gateway.demo) == 0 && length(aws_eip.demo) == 1
    error_message = "The existing direct-demo mode must not silently add CloudFront or NAT charges."
  }
  assert {
    condition     = length(aws_security_group.demo.ingress) == 2
    error_message = "Direct demo must retain the existing HTTP/HTTPS ingress."
  }
}
