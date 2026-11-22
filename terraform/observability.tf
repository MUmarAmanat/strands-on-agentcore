# Observability: traces (Transaction Search) and alerts by email.
#
# Traces: the agent's OpenTelemetry spans go to X-Ray. Transaction Search makes
# X-Ray store them in CloudWatch Logs, where AgentCore's GenAI Observability
# view reads them.
#
# Alerts: an alarm fires when the agent logs "Reminder run failed" or when
# Scheduler fails to invoke the runtime. Both send an email through SNS.

locals {
  # AgentCore creates this log group itself when the runtime is created, with
  # no retention. Terraform does not own it (creating it here would conflict
  # on a fresh apply); it only attaches the metric filter.
  runtime_log_group = "/aws/bedrock-agentcore/runtimes/${aws_bedrockagentcore_agent_runtime.this.agent_runtime_id}-DEFAULT"

  # Empty alert_email means: send alerts to the reminder recipient.
  alert_email = coalesce(var.alert_email, var.ses_recipient)

  # Custom metric namespace for the agent's own metrics.
  metric_namespace = var.project_name
}

# --- Transaction Search (account-wide, see enable_transaction_search) ---

# Lets X-Ray write spans into CloudWatch Logs. Must exist before the
# destination switch below.
resource "aws_cloudwatch_log_resource_policy" "xray_spans" {
  count = var.enable_transaction_search ? 1 : 0

  policy_name = "${var.project_name}-xray-transaction-search"
  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "TransactionSearchXRayAccess"
      Effect    = "Allow"
      Principal = { Service = "xray.amazonaws.com" }
      Action    = "logs:PutLogEvents"
      Resource = [
        "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:aws/spans:*",
        "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/application-signals/data:*",
      ]
      Condition = {
        # Only X-Ray acting for our own account.
        ArnLike      = { "aws:SourceArn" = "arn:aws:xray:${var.aws_region}:${local.account_id}:*" }
        StringEquals = { "aws:SourceAccount" = local.account_id }
      }
    }]
  })
}

# Turns Transaction Search on: X-Ray sends spans to CloudWatch Logs.
# Account-wide; terraform destroy does not switch it back to X-Ray.
resource "aws_xray_trace_segment_destination" "this" {
  count = var.enable_transaction_search ? 1 : 0

  destination = "CloudWatchLogs"

  depends_on = [aws_cloudwatch_log_resource_policy.xray_spans]
}

# --- Alerts ---

resource "aws_sns_topic" "alerts" {
  name = "${var.project_name}-alerts"
}

# AWS emails a confirmation link; no alert arrives until it is clicked.
resource "aws_sns_topic_subscription" "alert_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = local.alert_email
}

# Counts each "Reminder run failed" line the agent logs (app.py).
resource "aws_cloudwatch_log_metric_filter" "run_failed" {
  name           = "${var.project_name}-run-failed"
  log_group_name = local.runtime_log_group
  pattern        = "\"Reminder run failed\""

  metric_transformation {
    name      = "ReminderRunFailures"
    namespace = local.metric_namespace
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "run_failed" {
  alarm_name        = "${var.project_name}-run-failed"
  alarm_description = "The reminder agent logged 'Reminder run failed'. Check the runtime logs."

  namespace   = local.metric_namespace
  metric_name = aws_cloudwatch_log_metric_filter.run_failed.metric_transformation[0].name
  statistic   = "Sum"
  period      = 300
  threshold   = 1

  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching" # No log line = no failure.

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# Scheduler could not invoke the runtime (counted per failed attempt,
# retries included). Covers every schedule in the default group.
resource "aws_cloudwatch_metric_alarm" "scheduler_errors" {
  alarm_name        = "${var.project_name}-scheduler-errors"
  alarm_description = "EventBridge Scheduler failed to invoke the reminder agent."

  namespace   = "AWS/Scheduler"
  metric_name = "TargetErrorCount"
  dimensions = {
    ScheduleGroup = aws_scheduler_schedule.daily.group_name
  }
  statistic = "Sum"
  period    = 300
  threshold = 1

  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching" # No errors = no data point.

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}
