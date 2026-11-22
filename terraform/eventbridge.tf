# EventBridge Scheduler: calls InvokeAgentRuntime directly once a day (a
# universal target, no Lambda). Scheduler waits for the call and ignores the
# response; the agent acknowledges at once and runs in the background.

locals {
  schedule_name = "${var.project_name}-daily"

  # Scheduler assumes the role with aws:SourceArn set to the schedule
  # GROUP's ARN, not the schedule's, so the trust policy must match the group.
  schedule_group_arn = "arn:aws:scheduler:${var.aws_region}:${local.account_id}:schedule-group/default"

  # One fixed session ID for every scheduled run. Scheduler does NOT fill
  # <aws.scheduler.*> placeholders in this universal target, so a per-run ID
  # is not possible. Safe: the 5 min idle timeout ends each session before
  # the next day's run, and Memory skips a second email for the same date.
  # The API needs 33+ chars; letters, digits and hyphens only.
  session_id = "reminder-agent-scheduled-daily-run-session" # 42 chars
}

resource "aws_scheduler_schedule" "daily" {
  name        = local.schedule_name
  group_name  = "default"
  description = "Runs the reminder agent once a day."

  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = var.timezone # Same zone the agent uses for "today".
  state                        = var.schedule_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF" # Fire at the exact time.
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:bedrockagentcore:invokeAgentRuntime"
    role_arn = aws_iam_role.scheduler.arn

    # Request parameters of InvokeAgentRuntime. Fixed session ID (see
    # locals). Empty payload: the agent uses today in TIMEZONE, which avoids
    # converting Scheduler's UTC time.
    # Scheduler passes Payload through as written: plain JSON, not base64.
    input = jsonencode({
      AgentRuntimeArn  = aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
      RuntimeSessionId = local.session_id
      Payload          = "{}"
      ContentType      = "application/json"
    })

    retry_policy {
      # A retry cannot send a second email: run memory skips sent dates.
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }
  }

  # The first run must find the role's permissions in place.
  depends_on = [aws_iam_role_policy.scheduler]
}
