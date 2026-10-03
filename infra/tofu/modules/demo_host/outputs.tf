output "public_ip" {
  value = aws_eip.demo.public_ip
}

output "instance_id" {
  description = "Use with: aws ssm start-session --target <instance_id>"
  value       = aws_instance.demo.id
}
