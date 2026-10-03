# POC ONLY: one EC2 instance running the whole stack with docker compose, the cheap hackathon demo.
# Not the production design (docs/architecture.md; mapping in docs/poc_to_prod.md).
# No SSH: access is through SSM Session Manager. Bedrock and S3 are reached with the instance role.

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

data "aws_vpc" "default" {
  count   = var.enable_cloudfront ? 0 : 1
  default = true
}

data "aws_subnets" "default" {
  count = var.enable_cloudfront ? 0 : 1
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default[0].id]
  }
}

resource "aws_security_group" "demo" {
  name        = "${var.name}-demo"
  description = var.enable_cloudfront ? "CloudFront-only private demo ingress" : "HTTP/HTTPS in; everything out"
  vpc_id      = var.enable_cloudfront ? aws_vpc.demo[0].id : data.aws_vpc.default[0].id

  dynamic "ingress" {
    for_each = var.enable_cloudfront ? [] : [80, 443]
    content {
      description = ingress.value == 80 ? "HTTP (redirects to HTTPS)" : "HTTPS"
      from_port   = ingress.value
      to_port     = ingress.value
      protocol    = "tcp"
      cidr_blocks = var.allowed_cidrs
    }
  }

  dynamic "ingress" {
    for_each = var.enable_cloudfront ? [1] : []
    content {
      description     = "CloudFront origin-facing traffic only"
      from_port       = 80
      to_port         = 80
      protocol        = "tcp"
      prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront[0].id]
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_role" "demo" {
  name = "${var.name}-demo"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.demo.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "app" {
  name = "app"
  role = aws_iam_role.demo.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeAllowedModels"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = var.bedrock_model_arns
      },
      {
        Sid      = "ReadLake"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = ["arn:aws:s3:::${var.lake_bucket}", "arn:aws:s3:::${var.lake_bucket}/*"]
      },
    ]
  })
}

resource "aws_iam_instance_profile" "demo" {
  name = "${var.name}-demo"
  role = aws_iam_role.demo.name
}

resource "aws_instance" "demo" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  subnet_id              = var.enable_cloudfront ? aws_subnet.private[0].id : data.aws_subnets.default[0].ids[0]
  vpc_security_group_ids = [aws_security_group.demo.id]
  iam_instance_profile   = aws_iam_instance_profile.demo.name
  user_data              = file("${path.module}/user_data.sh")
  depends_on             = [aws_route_table_association.private, aws_route_table_association.public]

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2          # containers need one extra hop to reach the instance role
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.disk_gb
    encrypted   = true
  }

  tags = { Name = "${var.name}-demo" }
}

resource "aws_eip" "demo" {
  count    = var.enable_cloudfront ? 0 : 1
  instance = aws_instance.demo.id
  domain   = "vpc"
}

# Preserve the direct-demo Elastic IP address in existing state when adding the opt-in mode.
moved {
  from = aws_eip.demo
  to   = aws_eip.demo[0]
}
