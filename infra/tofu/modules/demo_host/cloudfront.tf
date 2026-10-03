# Browser TLS uses AWS's default certificate. Origin HTTP stays inside the private VPC.
resource "aws_cloudfront_vpc_origin" "demo" {
  count = var.enable_cloudfront ? 1 : 0
  vpc_origin_endpoint_config {
    name                   = "${var.name}-demo"
    arn                    = aws_instance.demo.arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only"
    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }
}

data "aws_cloudfront_cache_policy" "disabled" {
  count = var.enable_cloudfront ? 1 : 0
  name  = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "viewer" {
  count = var.enable_cloudfront ? 1 : 0
  name  = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "demo" {
  count               = var.enable_cloudfront ? 1 : 0
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${var.name} hackathon demo; no cached customer responses"
  price_class         = "PriceClass_100"
  wait_for_deployment = true

  origin {
    domain_name = aws_instance.demo.private_dns
    origin_id   = "demo-stack"
    vpc_origin_config {
      vpc_origin_id = aws_cloudfront_vpc_origin.demo[0].id
    }
  }

  default_cache_behavior {
    target_origin_id         = "demo-stack"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "PATCH", "POST", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled[0].id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.viewer[0].id
    compress                 = true
  }

  # Do not retain authorization or failure responses at the edge.
  dynamic "custom_error_response" {
    for_each = [400, 403, 404, 405, 414, 416, 500, 501, 502, 503, 504]
    content {
      error_code            = custom_error_response.value
      error_caching_min_ttl = 0
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }
  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
