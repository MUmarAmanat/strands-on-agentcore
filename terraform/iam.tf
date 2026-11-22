# Execution role that the AgentCore Runtime assumes. Least privilege: every
# statement except the AWS observability baseline names our own resources.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # "us.amazon.nova-pro-v1:0" -> "amazon.nova-pro-v1:0". A geo inference
  # profile routes to the base model in several regions, so both are needed.
  # Only known geo prefixes are stripped: a generic pattern would also strip
  # the provider ("amazon.") from a plain model ID.
  base_model_id = replace(
    var.bedrock_model_id, "/^(us|eu|apac|jp|au|ca|us-gov|global)\\./", ""
  )
}

resource "aws_iam_role" "runtime" {
  name        = "${var.project_name}-runtime"
  description = "Execution role for the reminder agent AgentCore Runtime."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
      # Only AgentCore in this account and region may assume the role.
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
        ArnLike = {
          "aws:SourceArn" = "arn:aws:bedrock-agentcore:${var.aws_region}:${local.account_id}:*"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "runtime" {
  name = "${var.project_name}-runtime"
  role = aws_iam_role.runtime.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # --- AWS baseline for AgentCore Runtime (logs, traces, metrics) ---
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:DescribeLogStreams",
          "logs:PutLogEvents",
        ]
        Resource = [
          "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*",
          "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*",
        ]
      },
      {
        Sid      = "DescribeLogGroups"
        Effect   = "Allow"
        Action   = "logs:DescribeLogGroups"
        Resource = "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:*"
      },
      {
        Sid    = "XRay"
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets",
        ]
        Resource = "*" # X-Ray has no resource-level permissions.
      },
      {
        Sid       = "Metrics"
        Effect    = "Allow"
        Action    = "cloudwatch:PutMetricData"
        Resource  = "*"
        Condition = { StringEquals = { "cloudwatch:namespace" = "bedrock-agentcore" } }
      },
      {
        Sid    = "WorkloadIdentity"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:GetWorkloadAccessToken",
          "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
        ]
        Resource = [
          "arn:aws:bedrock-agentcore:${var.aws_region}:${local.account_id}:workload-identity-directory/default",
          "arn:aws:bedrock-agentcore:${var.aws_region}:${local.account_id}:workload-identity-directory/default/workload-identity/${local.runtime_name}-*",
        ]
      },
      # --- App permissions, each scoped to our own resources ---
      {
        Sid    = "InvokeModel"
        Effect = "Allow"
        Action = "bedrock:InvokeModel" # Converse only (streaming=False).
        Resource = [
          "arn:aws:bedrock:*::foundation-model/${local.base_model_id}",
          "arn:aws:bedrock:${var.aws_region}:${local.account_id}:inference-profile/${var.bedrock_model_id}",
        ]
      },
      {
        Sid      = "ApplyGuardrail"
        Effect   = "Allow"
        Action   = "bedrock:ApplyGuardrail"
        Resource = aws_bedrock_guardrail.this.guardrail_arn
      },
      {
        Sid    = "RunMemory"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:CreateEvent",
          "bedrock-agentcore:ListEvents",
        ]
        Resource = aws_bedrockagentcore_memory.this.arn
      },
      {
        Sid      = "ReadSheetSecret"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = aws_secretsmanager_secret.google_sa.arn
      },
      {
        Sid      = "SendEmail"
        Effect   = "Allow"
        Action   = "ses:SendEmail"
        Resource = [for identity in aws_ses_email_identity.this : identity.arn]
      },
    ]
  })
}

# --- Role that EventBridge Scheduler assumes to start the runtime ---

resource "aws_iam_role" "scheduler" {
  name        = "${var.project_name}-scheduler"
  description = "Lets EventBridge Scheduler invoke the reminder agent runtime."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      # Only schedules in this account's default group may assume the role.
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = local.account_id
          "aws:SourceArn"     = local.schedule_group_arn
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${var.project_name}-scheduler"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "InvokeReminderRuntime"
      Effect = "Allow"
      Action = "bedrock-agentcore:InvokeAgentRuntime"
      # Calls are authorized on the runtime and on its endpoint.
      Resource = [
        aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn,
        "${aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn}/runtime-endpoint/*",
      ]
    }]
  })
}
