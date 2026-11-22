# Unit tests for the EventBridge Scheduler trigger (M8). Providers are
# mocked, so no AWS calls are made. Run: terraform test

mock_provider "aws" {
  # Same fake ARNs as core.tftest.hcl, for resources that validate ARN inputs.
  mock_resource "aws_bedrock_guardrail" {
    defaults = {
      guardrail_arn = "arn:aws:bedrock:us-east-1:123456789012:guardrail/mockguardrail"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/mock-role"
    }
  }

  mock_resource "aws_s3_bucket" {
    defaults = {
      bucket = "reminder-agent-artifacts-mock"
    }
  }

  mock_resource "aws_bedrockagentcore_agent_runtime" {
    defaults = {
      agent_runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/mock-runtime"
    }
  }

  # Alarm actions and the subscription validate the topic ARN.
  mock_resource "aws_sns_topic" {
    defaults = {
      arn = "arn:aws:sns:us-east-1:123456789012:reminder-agent-alerts"
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
}

mock_provider "external" {}

override_data {
  target = data.external.package
  values = {
    result = {
      path   = "build/agent.zip"
      sha256 = "abc123"
    }
  }
}

variables {
  aws_region         = "us-east-1"
  google_sheet_id    = "test-sheet-id"
  google_sa_key_path = "tests/fixtures/fake_service_account.txt"
  ses_sender         = "sender@example.com"
  ses_recipient      = "recipient@example.com"
}

# --- Schedule ---

run "schedule_daily_16_dubai" {
  # Default cron is 16:00 daily, in the agent's timezone.
  command = plan

  assert {
    condition     = aws_scheduler_schedule.daily.schedule_expression == "cron(0 16 * * ? *)"
    error_message = "Schedule must be cron(0 16 * * ? *)."
  }

  assert {
    condition     = aws_scheduler_schedule.daily.schedule_expression_timezone == "Asia/Dubai"
    error_message = "Schedule timezone must equal var.timezone (Asia/Dubai)."
  }
}

run "schedule_fires_exactly_on_time" {
  # Flexible time window is off.
  command = plan

  assert {
    condition     = aws_scheduler_schedule.daily.flexible_time_window[0].mode == "OFF"
    error_message = "Flexible time window must be OFF."
  }
}

run "schedule_enabled_by_default" {
  # A fresh deploy runs the agent daily.
  command = plan

  assert {
    condition     = aws_scheduler_schedule.daily.state == "ENABLED"
    error_message = "Schedule must be ENABLED by default."
  }
}

run "schedule_retry_policy" {
  # Two retries within one hour.
  command = plan

  assert {
    condition     = aws_scheduler_schedule.daily.target[0].retry_policy[0].maximum_retry_attempts == 2
    error_message = "Retry attempts must be 2."
  }

  assert {
    condition     = aws_scheduler_schedule.daily.target[0].retry_policy[0].maximum_event_age_in_seconds == 3600
    error_message = "Max event age must be 3600 seconds."
  }
}

# --- Target ---

run "target_is_invoke_agent_runtime" {
  # Universal target for InvokeAgentRuntime, no Lambda.
  command = plan

  assert {
    condition     = aws_scheduler_schedule.daily.target[0].arn == "arn:aws:scheduler:::aws-sdk:bedrockagentcore:invokeAgentRuntime"
    error_message = "Target must be the bedrockagentcore invokeAgentRuntime universal target."
  }
}

run "target_uses_scheduler_role" {
  # Scheduler assumes its own role, not the runtime's.
  command = apply

  assert {
    condition     = aws_scheduler_schedule.daily.target[0].role_arn == aws_iam_role.scheduler.arn
    error_message = "Target role must be the scheduler role."
  }
}

run "target_input_wired" {
  # Runtime ARN, fixed session ID (33+ chars, safe characters), empty JSON payload.
  command = apply

  assert {
    condition     = jsondecode(aws_scheduler_schedule.daily.target[0].input).AgentRuntimeArn == aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
    error_message = "AgentRuntimeArn must be the runtime ARN."
  }

  assert {
    condition     = jsondecode(aws_scheduler_schedule.daily.target[0].input).RuntimeSessionId == "reminder-agent-scheduled-daily-run-session"
    error_message = "RuntimeSessionId must be the fixed session ID."
  }

  assert {
    # API minimum; the 28-char placeholder alone failed this in the first deploy.
    condition     = length(jsondecode(aws_scheduler_schedule.daily.target[0].input).RuntimeSessionId) >= 33
    error_message = "Session ID must be 33+ chars."
  }

  assert {
    # No unfilled <aws.scheduler.*> placeholder, no '<', '>' or '.'.
    condition     = can(regex("^[A-Za-z0-9-]+$", jsondecode(aws_scheduler_schedule.daily.target[0].input).RuntimeSessionId))
    error_message = "Session ID must use only letters, digits and hyphens."
  }

  assert {
    condition     = jsondecode(aws_scheduler_schedule.daily.target[0].input).Payload == "{}"
    error_message = "Payload must be plain JSON {} (Scheduler passes it through, no base64)."
  }

  assert {
    condition     = jsondecode(aws_scheduler_schedule.daily.target[0].input).ContentType == "application/json"
    error_message = "ContentType must be application/json."
  }
}

# --- IAM ---

run "scheduler_role_trusts_only_our_schedule" {
  # Only Scheduler, from our account and our schedule, may assume the role.
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role.scheduler.assume_role_policy).Statement[0].Principal.Service == "scheduler.amazonaws.com"
    error_message = "Role must trust scheduler.amazonaws.com."
  }

  assert {
    condition     = jsondecode(aws_iam_role.scheduler.assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
    error_message = "Trust must be limited to our account."
  }

  assert {
    condition     = jsondecode(aws_iam_role.scheduler.assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceArn"] == "arn:aws:scheduler:us-east-1:123456789012:schedule-group/default"
    error_message = "Trust must be limited to our schedule ARN."
  }
}

run "scheduler_policy_least_privilege" {
  # One action, on our runtime and its endpoints only.
  command = apply

  assert {
    condition     = length(jsondecode(aws_iam_role_policy.scheduler.policy).Statement) == 1
    error_message = "Scheduler policy must have exactly one statement."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.scheduler.policy).Statement[0].Action == "bedrock-agentcore:InvokeAgentRuntime"
    error_message = "Only InvokeAgentRuntime is allowed."
  }

  assert {
    condition = toset(jsondecode(aws_iam_role_policy.scheduler.policy).Statement[0].Resource) == toset([
      "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/mock-runtime",
      "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/mock-runtime/runtime-endpoint/*",
    ])
    error_message = "Resources must be our runtime and its endpoints only."
  }
}

# --- Edge cases ---

run "schedule_can_be_paused" {
  # schedule_enabled = false disables the schedule without destroying it.
  command = plan

  variables {
    schedule_enabled = false
  }

  assert {
    condition     = aws_scheduler_schedule.daily.state == "DISABLED"
    error_message = "Schedule must be DISABLED when schedule_enabled is false."
  }
}

run "rejects_invalid_schedule_expression" {
  # Free text is not a schedule expression.
  command = plan

  variables {
    schedule_expression = "every day"
  }

  expect_failures = [var.schedule_expression]
}

run "output_exposes_schedule_name" {
  # The schedule_name output matches the schedule.
  command = apply

  assert {
    condition     = output.schedule_name == "reminder-agent-daily"
    error_message = "schedule_name output must be reminder-agent-daily."
  }
}
