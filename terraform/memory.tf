# AgentCore Memory: records which dates already got a reminder, so a
# Scheduler retry on the same day does not send a second email.

resource "aws_bedrockagentcore_memory" "this" {
  # Memory names do not allow hyphens.
  name        = "${replace(var.project_name, "-", "_")}_memory"
  description = "Reminder agent run log: dates already reminded."

  # Days. 7 is the minimum; a run only checks its own date.
  event_expiry_duration = 7
}
