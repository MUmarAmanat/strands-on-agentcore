# AgentCore Runtime: runs the agent zip from S3 (direct code deploy, no
# Docker). Every setting the agent reads comes in as an environment variable.

locals {
  # Runtime names allow letters, digits and underscores only.
  runtime_name = "${replace(var.project_name, "-", "_")}_runtime"
}

resource "aws_bedrockagentcore_agent_runtime" "this" {
  agent_runtime_name = local.runtime_name
  description        = "Reminder agent: reads due tasks and emails reminders."
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact {
    code_configuration {
      entry_point = ["main.py"] # Shim written by scripts/build_package.py.
      runtime     = "PYTHON_3_12"

      code {
        s3 {
          bucket = aws_s3_bucket.artifacts.bucket
          prefix = aws_s3_object.agent_zip.key
        }
      }
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  lifecycle_configuration {
    # Seconds. Stop idle sessions fast: one short run per day (cost).
    idle_runtime_session_timeout = 300
    # AWS default (8 h). Set explicitly: when left null, the AWS provider
    # fails with "inconsistent result after apply" (provider bug).
    max_lifetime = 28800
  }

  environment_variables = {
    ENVIRONMENT         = var.environment
    SOURCE_TYPE         = "google_sheets"
    GOOGLE_SHEET_ID     = var.google_sheet_id
    GOOGLE_SHEET_RANGE  = var.google_sheet_range
    SHEET_SECRET_NAME   = aws_secretsmanager_secret.google_sa.name
    BEDROCK_MODEL_ID    = var.bedrock_model_id
    SES_SENDER          = var.ses_sender
    SES_RECIPIENT       = var.ses_recipient
    AWS_REGION          = var.aws_region
    TIMEZONE            = var.timezone
    LLM_TIMEOUT_SECONDS = tostring(var.llm_timeout_seconds)
    MAX_RETRIES         = tostring(var.max_retries)
    MEMORY_ID           = aws_bedrockagentcore_memory.this.id
    GUARDRAIL_ID        = aws_bedrock_guardrail.this.guardrail_id
    GUARDRAIL_VERSION   = aws_bedrock_guardrail_version.this.version
  }

  # The first run must find its permissions and the key value in place.
  depends_on = [
    aws_iam_role_policy.runtime,
    aws_secretsmanager_secret_version.google_sa,
  ]
}
