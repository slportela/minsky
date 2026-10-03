# Temporary remote smoke only (ADR 0011). No EC2 migration or runtime AWS credentials.
resource "aws_lightsail_instance" "smoke" {
  name              = var.name
  availability_zone = var.availability_zone
  blueprint_id      = var.blueprint_id
  bundle_id         = var.bundle_id
  ip_address_type   = "ipv4"
  user_data         = file("${path.module}/user_data.sh")
}
resource "aws_lightsail_static_ip" "smoke" {
  name = "${var.name}-ip"
}
resource "aws_lightsail_static_ip_attachment" "smoke" {
  static_ip_name = aws_lightsail_static_ip.smoke.name
  instance_name  = aws_lightsail_instance.smoke.name
}
resource "aws_lightsail_instance_public_ports" "smoke" {
  instance_name = aws_lightsail_instance.smoke.name
  port_info {
    from_port = 80
    to_port   = 80
    protocol  = "tcp"
    cidrs     = ["0.0.0.0/0"]
  }
  port_info {
    from_port = 22
    to_port   = 22
    protocol  = "tcp"
    cidrs     = var.ssh_cidrs
  }
}
resource "aws_lightsail_distribution" "smoke" {
  name            = "${var.name}-cdn"
  bundle_id       = "small_1_0"
  ip_address_type = "ipv4"
  is_enabled      = true
  origin {
    name            = aws_lightsail_instance.smoke.name
    region_name     = var.region
    protocol_policy = "http-only"
  }
  default_cache_behavior { behavior = "dont-cache" }
  cache_behavior_settings {
    allowed_http_methods = "GET,HEAD,OPTIONS,PUT,PATCH,POST,DELETE"
    cached_http_methods  = "GET,HEAD"
    minimum_ttl          = 0
    default_ttl          = 0
    maximum_ttl          = 0
    forwarded_headers {
      option             = "allow-list"
      headers_allow_list = ["Authorization", "Origin", "Accept", "Accept-Language"]
    }
    forwarded_cookies { option = "all" }
    forwarded_query_strings { option = true }
  }
  depends_on = [aws_lightsail_static_ip_attachment.smoke, aws_lightsail_instance_public_ports.smoke]
}
output "demo_url" { value = "https://${aws_lightsail_distribution.smoke.domain_name}" }
output "instance_name" { value = aws_lightsail_instance.smoke.name }
output "public_ip" { value = aws_lightsail_static_ip.smoke.ip_address }
