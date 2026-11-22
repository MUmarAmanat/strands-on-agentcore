# Build the agent zip and upload it to S3 for AgentCore direct code deploy.

# Runs the build script on every plan. The zip is deterministic, so an
# unchanged codebase gives the same sha256 and nothing is redeployed.
data "external" "package" {
  program     = ["uv", "run", "python", "scripts/build_package.py"]
  working_dir = "${path.module}/.."
}

resource "aws_s3_bucket" "artifacts" {
  bucket_prefix = "${var.project_name}-artifacts-"

  # Let `terraform destroy` remove the bucket even if it still holds zips.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# The key carries the sha256: AgentCore caches zips by key, so new code
# needs a new key. Terraform deletes the old object when the key changes.
resource "aws_s3_object" "agent_zip" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "agent-${data.external.package.result.sha256}.zip"
  source = data.external.package.result.path
}
