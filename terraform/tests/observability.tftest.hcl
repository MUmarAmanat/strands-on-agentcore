# Unit tests for observability: Transaction Search, the failure metric
# filter, SNS email alerts and the two alarms. Providers are mocked, so no
# AWS calls are made. Run: terraform test

mock_provider "aws" {
  # Same fake ARNs as the other test files, for inputs that validate ARNs.
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

  # Fixed runtime ID, so the log group name can be checked exactly.
  mock_resource "aws_bedrockagentcore_agent_runtime" {
    defaults = {
      agent_runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/mock-runtime"
      agent_runtime_id  = "mock_runtime-abc123"
    }
  }

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

# --- Transaction Search ---

run "transaction_search_on_by_default" {
  # Both the log policy and the destination switch are created.
  command = apply

  assert {
    condition     = length(aws_cloudwatch_log_resource_policy.xray_spans) == 1
    error_message = "X-Ray log resource policy must be created by default."
  }

  assert {
    condition     = aws_xray_trace_segment_destination.this[0].destination == "CloudWatchLogs"
    error_message = "Trace segment destination must be CloudWatchLogs."
  }
}

run "xray_policy_scoped" {
  # Only X-Ray, only PutLogEvents, only the spans and application-signals groups.
  command = apply

  assert {
    condition     = jsondecode(aws_cloudwatch_log_resource_policy.xray_spans[0].policy_document).Statement[0].Principal.Service == "xray.amazonaws.com"
    error_message = "Policy principal must be xray.amazonaws.com."
  }

  assert {
    condition     = jsondecode(aws_cloudwatch_log_resource_policy.xray_spans[0].policy_document).Statement[0].Action == "logs:PutLogEvents"
    error_message = "Policy must allow logs:PutLogEvents only."
  }

  assert {
    condition = toset(jsondecode(aws_cloudwatch_log_resource_policy.xray_spans[0].policy_document).Statement[0].Resource) == toset([
      "arn:aws:logs:us-east-1:123456789012:log-group:aws/spans:*",
      "arn:aws:logs:us-east-1:123456789012:log-group:/aws/application-signals/data:*",
    ])
    error_message = "Policy resources must be the spans and application-signals log groups only."
  }

  assert {
    condition     = jsondecode(aws_cloudwatch_log_resource_policy.xray_spans[0].policy_document).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
    error_message = "Policy must be limited to our account."
  }
}

# --- Metric filter ---

run "metric_filter_on_runtime_log_group" {
  # The log group AgentCore creates, named after the runtime ID.
  command = apply

  assert {
    condition     = aws_cloudwatch_log_metric_filter.run_failed.log_group_name == "/aws/bedrock-agentcore/runtimes/mock_runtime-abc123-DEFAULT"
    error_message = "Metric filter must use the runtime's -DEFAULT log group."
  }
}

run "metric_filter_matches_app_log" {
  # Same text app.py logs on failure; counts 1 per match.
  command = apply

  assert {
    condition     = strcontains(aws_cloudwatch_log_metric_filter.run_failed.pattern, "Reminder run failed")
    error_message = "Pattern must match 'Reminder run failed'."
  }

  assert {
    condition     = aws_cloudwatch_log_metric_filter.run_failed.metric_transformation[0].name == "ReminderRunFailures"
    error_message = "Metric name must be ReminderRunFailures."
  }

  assert {
    condition     = aws_cloudwatch_log_metric_filter.run_failed.metric_transformation[0].value == "1"
    error_message = "Each match must count as 1."
  }
}

# --- Alarms ---

run "run_failed_alarm_wired" {
  # One failure in 5 min alarms; quiet days stay OK; alarm and recovery emailed.
  command = apply

  assert {
    condition     = aws_cloudwatch_metric_alarm.run_failed.metric_name == "ReminderRunFailures"
    error_message = "Alarm must watch ReminderRunFailures."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.run_failed.threshold == 1 && aws_cloudwatch_metric_alarm.run_failed.comparison_operator == "GreaterThanOrEqualToThreshold"
    error_message = "Alarm must fire on 1 or more failures."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.run_failed.treat_missing_data == "notBreaching"
    error_message = "Missing data must count as OK."
  }

  assert {
    condition     = toset(aws_cloudwatch_metric_alarm.run_failed.alarm_actions) == toset([aws_sns_topic.alerts.arn]) && toset(aws_cloudwatch_metric_alarm.run_failed.ok_actions) == toset([aws_sns_topic.alerts.arn])
    error_message = "Alarm and OK actions must notify the alerts topic."
  }
}

run "scheduler_alarm_wired" {
  # Scheduler's own failure metric, for the default schedule group.
  command = apply

  assert {
    condition     = aws_cloudwatch_metric_alarm.scheduler_errors.namespace == "AWS/Scheduler" && aws_cloudwatch_metric_alarm.scheduler_errors.metric_name == "TargetErrorCount"
    error_message = "Alarm must watch AWS/Scheduler TargetErrorCount."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.scheduler_errors.dimensions["ScheduleGroup"] == "default"
    error_message = "Alarm must be scoped to the default schedule group."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.scheduler_errors.treat_missing_data == "notBreaching"
    error_message = "Missing data must count as OK."
  }

  assert {
    condition     = toset(aws_cloudwatch_metric_alarm.scheduler_errors.alarm_actions) == toset([aws_sns_topic.alerts.arn])
    error_message = "Alarm must notify the alerts topic."
  }
}

# --- Alert email ---

run "alert_email_defaults_to_recipient" {
  # Empty alert_email falls back to the reminder recipient.
  command = plan

  assert {
    condition     = aws_sns_topic_subscription.alert_email.protocol == "email"
    error_message = "Subscription protocol must be email."
  }

  assert {
    condition     = aws_sns_topic_subscription.alert_email.endpoint == "recipient@example.com"
    error_message = "Empty alert_email must fall back to ses_recipient."
  }
}

# --- Edge cases ---

run "alert_email_override" {
  # A set alert_email wins over the recipient.
  command = plan

  variables {
    alert_email = "ops@example.com"
  }

  assert {
    condition     = aws_sns_topic_subscription.alert_email.endpoint == "ops@example.com"
    error_message = "alert_email must be used when set."
  }
}

run "transaction_search_can_be_disabled" {
  # false skips both account-wide resources.
  command = plan

  variables {
    enable_transaction_search = false
  }

  assert {
    condition     = length(aws_cloudwatch_log_resource_policy.xray_spans) == 0 && length(aws_xray_trace_segment_destination.this) == 0
    error_message = "No Transaction Search resources when disabled."
  }
}

run "rejects_invalid_alert_email" {
  # Free text is not an email address.
  command = plan

  variables {
    alert_email = "not-an-email"
  }

  expect_failures = [var.alert_email]
}
