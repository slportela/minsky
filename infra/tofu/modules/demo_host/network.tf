# A private origin keeps the Compose stack inaccessible from the public internet.
data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  count = var.enable_cloudfront ? 1 : 0
  name  = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_vpc" "demo" {
  count                = var.enable_cloudfront ? 1 : 0
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.name}-demo" }
}

resource "aws_internet_gateway" "demo" {
  count  = var.enable_cloudfront ? 1 : 0
  vpc_id = aws_vpc.demo[0].id
}

resource "aws_subnet" "public" {
  count             = var.enable_cloudfront ? 1 : 0
  vpc_id            = aws_vpc.demo[0].id
  cidr_block        = "10.42.0.0/24"
  availability_zone = data.aws_availability_zones.available.names[0]
}

resource "aws_subnet" "private" {
  count                   = var.enable_cloudfront ? 1 : 0
  vpc_id                  = aws_vpc.demo[0].id
  cidr_block              = "10.42.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = false
}

resource "aws_eip" "nat" {
  count  = var.enable_cloudfront ? 1 : 0
  domain = "vpc"
}

resource "aws_nat_gateway" "demo" {
  count         = var.enable_cloudfront ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id
  depends_on    = [aws_internet_gateway.demo]
}

resource "aws_route_table" "public" {
  count  = var.enable_cloudfront ? 1 : 0
  vpc_id = aws_vpc.demo[0].id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.demo[0].id
  }
}

resource "aws_route_table" "private" {
  count  = var.enable_cloudfront ? 1 : 0
  vpc_id = aws_vpc.demo[0].id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.demo[0].id
  }
}

resource "aws_route_table_association" "public" {
  count          = var.enable_cloudfront ? 1 : 0
  subnet_id      = aws_subnet.public[0].id
  route_table_id = aws_route_table.public[0].id
}

resource "aws_route_table_association" "private" {
  count          = var.enable_cloudfront ? 1 : 0
  subnet_id      = aws_subnet.private[0].id
  route_table_id = aws_route_table.private[0].id
}
