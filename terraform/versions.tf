# Terraform and provider versions, plus the AWS provider configuration.

terraform {
  # 1.7 is the first version with mock_provider for `terraform test`.
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 6.22 added code_configuration (AgentCore zip deploy, no Docker).
      version = "~> 6.22"
    }
    external = {
      source  = "hashicorp/external"
      version = "~> 2.3"
    }
  }
}

provider "aws" {
  region = var.aws_region

  # Tag every resource so the whole app is easy to find and cost-track.
  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
