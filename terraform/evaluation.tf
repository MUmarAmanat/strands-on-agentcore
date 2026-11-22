# Online evaluation: AgentCore scores every agent run with built-in
# LLM-as-a-judge evaluators. It reads the traces that observability.tf
# enables (Transaction Search is a prerequisite) and writes the scores to a
# results log group and to CloudWatch metrics
# (namespace Bedrock-AgentCore/Evaluations). No agent code is involved.

locals {
  # Name rule: letters, digits and underscores only (no hyphens).
  evaluation_config_name = "${replace(var.project_name, "-", "_")}_eval"

  # AgentCore Runtime agents report this service name in their traces.
  evaluation_service_name = "${local.runtime_name}.DEFAULT"
}

# Role that AgentCore assumes to read traces, call the judge model and
# write the results.
resource "aws_iam_role" "evaluation" {
  count = var.enable_online_evaluation ? 1 : 0

  name        = "${var.project_name}-evaluation"
  description = "Lets AgentCore online evaluation score the reminder agent's runs."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
      # Only online evaluation configs in this account and region.
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
        ArnLike = {
          "aws:SourceArn" = "arn:aws:bedrock-agentcore:${var.aws_region}:${local.account_id}:online-evaluation-config/*"
        }
      }
    }]
  })
}

# Based on the AWS docs policy for the evaluation role, scoped to this
# account and region where the action allows it.
resource "aws_iam_role_policy" "evaluation" {
  count = var.enable_online_evaluation ? 1 : 0

  name = "${var.project_name}-evaluation"
  role = aws_iam_role.evaluation[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Find and query the agent's traces (Logs Insights).
        Sid    = "ReadTraces"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogGroups",
          "logs:StartQuery",
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:*"
      },
      {
        Sid      = "ReadQueryResults"
        Effect   = "Allow"
        Action   = "logs:GetQueryResults"
        Resource = "*" # No resource-level permissions for this action.
      },
      {
        # Scores go only to the evaluations log groups.
        Sid    = "WriteResults"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/bedrock-agentcore/evaluations/*"
      },
      {
        # Index the spans so sessions can be looked up.
        Sid    = "IndexSpans"
        Effect = "Allow"
        Action = [
          "logs:DescribeIndexPolicies",
          "logs:PutIndexPolicy",
        ]
        Resource = [
          "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:aws/spans",
          "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:aws/spans:*",
        ]
      },
      {
        # Built-in judges pick their own model, so models cannot be named
        # here. Any region: an inference profile may route elsewhere.
        Sid    = "InvokeJudgeModel"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:${var.aws_region}:${local.account_id}:inference-profile/*",
        ]
      },
    ]
  })
}

resource "aws_bedrockagentcore_online_evaluation_config" "this" {
  count = var.enable_online_evaluation ? 1 : 0

  online_evaluation_config_name = local.evaluation_config_name
  description                   = "Scores every reminder agent run with built-in evaluators."
  enable_on_create              = true
  evaluation_execution_role_arn = aws_iam_role.evaluation[0].arn

  data_source_config {
    cloudwatch_logs {
      log_group_names = [local.runtime_log_group]
      service_names   = [local.evaluation_service_name]
    }
  }

  dynamic "evaluator" {
    for_each = var.evaluators
    content {
      evaluator_id = evaluator.value
    }
  }

  rule {
    # One run a day, so score every run.
    sampling_config {
      sampling_percentage = 100
    }
  }

  # The role must have its permissions, and traces must reach CloudWatch,
  # before the config is created.
  depends_on = [
    aws_iam_role_policy.evaluation,
    aws_xray_trace_segment_destination.this,
  ]
}
