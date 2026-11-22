# Unit tests for online evaluation: the evaluation role and the online
# evaluation config. Providers are mocked, so no AWS calls are made.
# Run: terraform test

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

# --- Config ---

run "eval_on_by_default" {
  # One config, every run scored, enabled straight away.
  command = apply

  assert {
    condition     = length(aws_bedrockagentcore_online_evaluation_config.this) == 1
    error_message = "Online evaluation config must be created by default."
  }

  assert {
    condition     = aws_bedrockagentcore_online_evaluation_config.this[0].rule[0].sampling_config[0].sampling_percentage == 100
    error_message = "Sampling must be 100% (one run a day)."
  }

  assert {
    condition     = aws_bedrockagentcore_online_evaluation_config.this[0].enable_on_create == true
    error_message = "Config must be enabled on create."
  }
}

run "eval_reads_runtime_logs" {
  # Reads the runtime's log group, filtered to the runtime's service name.
  command = apply

  assert {
    condition     = aws_bedrockagentcore_online_evaluation_config.this[0].data_source_config[0].cloudwatch_logs[0].log_group_names == tolist(["/aws/bedrock-agentcore/runtimes/mock_runtime-abc123-DEFAULT"])
    error_message = "Data source must be the runtime's -DEFAULT log group."
  }

  assert {
    condition     = aws_bedrockagentcore_online_evaluation_config.this[0].data_source_config[0].cloudwatch_logs[0].service_names == tolist(["reminder_agent_runtime.DEFAULT"])
    error_message = "Service name must be <runtime_name>.DEFAULT."
  }
}

run "default_evaluators" {
  # Exactly the five built-ins chosen for this agent. Evaluators are a set
  # (no order), so compare as sets.
  command = apply

  assert {
    condition = toset([for e in aws_bedrockagentcore_online_evaluation_config.this[0].evaluator : e.evaluator_id]) == toset([
      "Builtin.GoalSuccessRate",
      "Builtin.InstructionFollowing",
      "Builtin.Faithfulness",
      "Builtin.ToolSelectionAccuracy",
      "Builtin.ToolParameterAccuracy",
    ])
    error_message = "Config must use the five default built-in evaluators."
  }
}

# --- Role ---

run "eval_role_trust_scoped" {
  # Only AgentCore online evaluation configs in our account may assume it.
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role.evaluation[0].assume_role_policy).Statement[0].Principal.Service == "bedrock-agentcore.amazonaws.com"
    error_message = "Trust principal must be bedrock-agentcore.amazonaws.com."
  }

  assert {
    condition     = jsondecode(aws_iam_role.evaluation[0].assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
    error_message = "Trust must be limited to our account."
  }

  assert {
    condition     = jsondecode(aws_iam_role.evaluation[0].assume_role_policy).Statement[0].Condition.ArnLike["aws:SourceArn"] == "arn:aws:bedrock-agentcore:us-east-1:123456789012:online-evaluation-config/*"
    error_message = "Trust must be limited to online evaluation configs."
  }
}

run "eval_role_least_privilege" {
  # Writes only to evaluations log groups; indexes only aws/spans.
  command = apply

  assert {
    condition = one([
      for s in jsondecode(aws_iam_role_policy.evaluation[0].policy).Statement : s.Resource if s.Sid == "WriteResults"
    ]) == "arn:aws:logs:us-east-1:123456789012:log-group:/aws/bedrock-agentcore/evaluations/*"
    error_message = "Results may only be written to /aws/bedrock-agentcore/evaluations/*."
  }

  assert {
    condition = toset(one([
      for s in jsondecode(aws_iam_role_policy.evaluation[0].policy).Statement : s.Resource if s.Sid == "IndexSpans"
      ])) == toset([
      "arn:aws:logs:us-east-1:123456789012:log-group:aws/spans",
      "arn:aws:logs:us-east-1:123456789012:log-group:aws/spans:*",
    ])
    error_message = "Index policy may only be set on aws/spans."
  }
}

# --- Edge cases ---

run "eval_can_be_disabled" {
  # false skips the config and its role.
  command = plan

  variables {
    enable_online_evaluation = false
  }

  assert {
    condition     = length(aws_bedrockagentcore_online_evaluation_config.this) == 0 && length(aws_iam_role.evaluation) == 0 && length(aws_iam_role_policy.evaluation) == 0
    error_message = "No evaluation resources when disabled."
  }
}

run "rejects_empty_evaluators" {
  # At least one evaluator is required.
  command = plan

  variables {
    evaluators = []
  }

  expect_failures = [var.evaluators]
}

run "rejects_non_builtin_evaluator" {
  # Custom or third-party IDs are not allowed here.
  command = plan

  variables {
    evaluators = ["ThirdParty.DeepEval.Bias"]
  }

  expect_failures = [var.evaluators]
}

run "rejects_more_than_ten_evaluators" {
  # The API allows at most 10 evaluators per config.
  command = plan

  variables {
    evaluators = [for i in range(11) : "Builtin.Helpfulness"]
  }

  expect_failures = [var.evaluators]
}

run "config_name_valid" {
  # Hyphens in project_name become underscores (API name rule).
  command = plan

  assert {
    condition     = aws_bedrockagentcore_online_evaluation_config.this[0].online_evaluation_config_name == "reminder_agent_eval"
    error_message = "Config name must be reminder_agent_eval."
  }

  assert {
    condition     = can(regex("^[A-Za-z][A-Za-z0-9_]{0,47}$", aws_bedrockagentcore_online_evaluation_config.this[0].online_evaluation_config_name))
    error_message = "Config name must be letters, digits and underscores, max 48 chars."
  }
}
