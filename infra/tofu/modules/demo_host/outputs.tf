output "public_ip" {
  value = var.enable_cloudfront ? null : aws_eip.demo[0].public_ip
}

output "instance_id" {
  description = "Use with: aws ssm start-session --target <instance_id>"
  value       = aws_instance.demo.id
}

output "demo_url" {
  description = "Browser URL; null for the legacy direct demo until a domain is configured"
  value       = var.enable_cloudfront ? "https://${aws_cloudfront_distribution.demo[0].domain_name}/chat" : null
}
