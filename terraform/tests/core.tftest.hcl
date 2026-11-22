# Unit tests for the core infrastructure. Providers are mocked, so no AWS
# calls are made and the build script never runs. Run: terraform test

mock_provider "aws" {
  # Mocks fill computed values with random strings; resources that validate
  # an ARN input need a real-looking one. 123456789012 is the AWS doc placeholder.
  mock_resource "aws_bedrock_guardrail" {
    defaults = {
      guardrail_arn = "arn:aws:bedrock:us-east-1:123456789012:guardrail/mockguardrail"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/mock-runtime"
    }
  }

  # A valid bucket name, since the runtime's S3 bucket field may be validated.
  mock_resource "aws_s3_bucket" {
    defaults = {
      bucket = "reminder-agent-artifacts-mock"
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

# Stand-in for the build script's JSON output.
override_data {
  target = data.external.package
  values = {
    result = {
      path   = "build/agent.zip"
      sha256 = "abc123"
    }
  }
}

# Valid baseline inputs; each run overrides only what it tests.
variables {
  aws_region         = "us-east-1"
  google_sheet_id    = "test-sheet-id"
  google_sa_key_path = "tests/fixtures/fake_service_account.txt"
  ses_sender         = "sender@example.com"
  ses_recipient      = "recipient@example.com"
}

run "zip_key_contains_sha" {
  # The S3 key is agent-<sha>.zip and the upload uses the built file.
  command = apply

  assert {
    condition     = aws_s3_object.agent_zip.key == "agent-abc123.zip"
    error_message = "Zip key must be agent-<sha256>.zip."
  }

  assert {
    condition     = aws_s3_object.agent_zip.source == "build/agent.zip"
    error_message = "Zip source must be the path from the build script."
  }

  assert {
    condition     = aws_s3_object.agent_zip.bucket == aws_s3_bucket.artifacts.id
    error_message = "Zip must go to the artifacts bucket."
  }
}

run "bucket_is_private_and_named" {
  # All public access is blocked and the name starts with the project.
  command = plan

  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.artifacts.block_public_acls,
      aws_s3_bucket_public_access_block.artifacts.block_public_policy,
      aws_s3_bucket_public_access_block.artifacts.ignore_public_acls,
      aws_s3_bucket_public_access_block.artifacts.restrict_public_buckets,
    ])
    error_message = "Artifacts bucket must block all public access."
  }

  assert {
    condition     = aws_s3_bucket.artifacts.bucket_prefix == "reminder-agent-artifacts-"
    error_message = "Bucket prefix must start with the project name."
  }
}

run "rejects_invalid_email" {
  # A sender without an @ fails validation.
  command = plan

  variables {
    ses_sender = "not-an-email"
  }

  expect_failures = [var.ses_sender]
}

run "rejects_third_party_model" {
  # Marketplace-billed models are not allowed.
  command = plan

  variables {
    bedrock_model_id = "anthropic.claude-3-haiku-20240307-v1:0"
  }

  expect_failures = [var.bedrock_model_id]
}

run "accepts_cross_region_profile" {
  # A geo inference profile for an Amazon model is valid.
  command = plan

  variables {
    bedrock_model_id = "us.amazon.nova-pro-v1:0"
  }

  assert {
    condition     = var.bedrock_model_id == "us.amazon.nova-pro-v1:0"
    error_message = "Cross-region Amazon profile should be accepted."
  }
}

run "rejects_missing_key_file" {
  # A key path that does not exist fails validation.
  command = plan

  variables {
    google_sa_key_path = "tests/fixtures/does_not_exist.txt"
  }

  expect_failures = [var.google_sa_key_path]
}

run "rejects_non_json_key_file" {
  # A key file that is not JSON fails validation.
  command = plan

  variables {
    google_sa_key_path = "tests/fixtures/not_json.txt"
  }

  expect_failures = [var.google_sa_key_path]
}

run "rejects_unknown_environment" {
  # Only dev, staging and prod are allowed.
  command = plan

  variables {
    environment = "test"
  }

  expect_failures = [var.environment]
}

run "rejects_invalid_project_name" {
  # Uppercase and underscores are not allowed in names.
  command = plan

  variables {
    project_name = "Reminder_Agent"
  }

  expect_failures = [var.project_name]
}


run "secret_holds_key_file" {
  # Secret is named per project, deletes at once, and stores the key file.
  command = plan

  assert {
    condition     = aws_secretsmanager_secret.google_sa.name == "reminder-agent/google-service-account"
    error_message = "Secret name must be <project>/google-service-account."
  }

  assert {
    condition     = aws_secretsmanager_secret.google_sa.recovery_window_in_days == 0
    error_message = "Recovery window must be 0 so re-apply can reuse the name."
  }

  assert {
    condition     = aws_secretsmanager_secret_version.google_sa.secret_string == file("tests/fixtures/fake_service_account.txt")
    error_message = "Secret value must be the key file content."
  }
}

run "ses_two_identities_for_two_emails" {
  # Different sender and recipient get one identity each.
  command = plan

  assert {
    condition     = length(aws_ses_email_identity.this) == 2
    error_message = "Expected 2 SES identities."
  }
}

run "ses_one_identity_for_same_email" {
  # Same address for both gives one identity, not a clash.
  command = plan

  variables {
    ses_recipient = "sender@example.com"
  }

  assert {
    condition     = length(aws_ses_email_identity.this) == 1
    error_message = "Expected 1 SES identity when sender equals recipient."
  }
}

run "guardrail_prompt_attack_input_only" {
  # Prompt attack is HIGH on input and NONE on output (API rule).
  command = plan

  assert {
    condition = anytrue([
      for f in one(aws_bedrock_guardrail.this.content_policy_config).filters_config :
      f.type == "PROMPT_ATTACK" && f.input_strength == "HIGH" && f.output_strength == "NONE"
    ])
    error_message = "PROMPT_ATTACK must be input HIGH, output NONE."
  }
}

run "guardrail_content_filters_medium" {
  # The five content filters are MEDIUM both ways.
  command = plan

  assert {
    condition = length([
      for f in one(aws_bedrock_guardrail.this.content_policy_config).filters_config :
      f if contains(["HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT"], f.type)
      && f.input_strength == "MEDIUM" && f.output_strength == "MEDIUM"
    ]) == 5
    error_message = "All five content filters must be MEDIUM in and out."
  }
}

run "guardrail_version_uses_guardrail" {
  # The published version belongs to this guardrail.
  command = apply

  assert {
    condition     = aws_bedrock_guardrail_version.this.guardrail_arn == aws_bedrock_guardrail.this.guardrail_arn
    error_message = "Guardrail version must reference the guardrail ARN."
  }
}

run "memory_name_and_expiry" {
  # Hyphens become underscores; events expire after 7 days.
  command = plan

  assert {
    condition     = aws_bedrockagentcore_memory.this.name == "reminder_agent_memory"
    error_message = "Memory name must be reminder_agent_memory."
  }

  assert {
    condition     = aws_bedrockagentcore_memory.this.event_expiry_duration == 7
    error_message = "Memory events must expire after 7 days."
  }
}

# --- 7c: IAM, runtime, outputs ---

run "role_trusts_only_agentcore_in_account" {
  # Only the AgentCore service, from our account, may assume the role.
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role.runtime.assume_role_policy).Statement[0].Principal.Service == "bedrock-agentcore.amazonaws.com"
    error_message = "Role must trust bedrock-agentcore.amazonaws.com."
  }

  assert {
    condition     = jsondecode(aws_iam_role.runtime.assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
    error_message = "Trust must be limited to our account."
  }
}

run "policy_scoped_to_own_resources" {
  # Secret, guardrail and memory statements name only our resources.
  command = apply

  assert {
    condition = one([
      for s in jsondecode(aws_iam_role_policy.runtime.policy).Statement : s.Resource
      if s.Sid == "ReadSheetSecret"
    ]) == aws_secretsmanager_secret.google_sa.arn
    error_message = "Secret access must be limited to our secret."
  }

  assert {
    condition = one([
      for s in jsondecode(aws_iam_role_policy.runtime.policy).Statement : s.Resource
      if s.Sid == "ApplyGuardrail"
    ]) == aws_bedrock_guardrail.this.guardrail_arn
    error_message = "ApplyGuardrail must be limited to our guardrail."
  }

  assert {
    condition = one([
      for s in jsondecode(aws_iam_role_policy.runtime.policy).Statement : s.Resource
      if s.Sid == "RunMemory"
    ]) == aws_bedrockagentcore_memory.this.arn
    error_message = "Memory access must be limited to our memory."
  }
}

run "invoke_model_scoped_to_configured_model" {
  # A geo profile allows exactly that profile and its base model.
  command = apply

  variables {
    bedrock_model_id = "us.amazon.nova-pro-v1:0"
  }

  assert {
    condition = toset(one([
      for s in jsondecode(aws_iam_role_policy.runtime.policy).Statement : s.Resource
      if s.Sid == "InvokeModel"
      ])) == toset([
      "arn:aws:bedrock:*::foundation-model/amazon.nova-pro-v1:0",
      "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.amazon.nova-pro-v1:0",
    ])
    error_message = "InvokeModel must cover only the configured profile and base model."
  }
}

run "invoke_model_keeps_provider_of_plain_model_id" {
  # Regression: a plain ID (the default) must keep its "amazon." provider.
  command = apply

  variables {
    bedrock_model_id = "amazon.nova-pro-v1:0"
  }

  assert {
    condition = contains(one([
      for s in jsondecode(aws_iam_role_policy.runtime.policy).Statement : s.Resource
      if s.Sid == "InvokeModel"
    ]), "arn:aws:bedrock:*::foundation-model/amazon.nova-pro-v1:0")
    error_message = "InvokeModel must allow the plain model ID unchanged."
  }
}

run "runtime_uses_zip_code_deploy" {
  # Python 3.12 zip from our bucket and key, started via main.py.
  command = apply

  assert {
    condition     = aws_bedrockagentcore_agent_runtime.this.agent_runtime_artifact[0].code_configuration[0].entry_point == tolist(["main.py"])
    error_message = "Entry point must be main.py."
  }

  assert {
    condition     = aws_bedrockagentcore_agent_runtime.this.agent_runtime_artifact[0].code_configuration[0].runtime == "PYTHON_3_12"
    error_message = "Runtime must be PYTHON_3_12."
  }

  assert {
    condition     = aws_bedrockagentcore_agent_runtime.this.agent_runtime_artifact[0].code_configuration[0].code[0].s3[0].prefix == "agent-abc123.zip"
    error_message = "Runtime must load the agent-<sha>.zip key."
  }

  assert {
    condition     = aws_bedrockagentcore_agent_runtime.this.agent_runtime_artifact[0].code_configuration[0].code[0].s3[0].bucket == "reminder-agent-artifacts-mock"
    error_message = "Runtime must load from the artifacts bucket."
  }
}

run "runtime_env_vars_wired" {
  # Env vars come from the created resources, not hard-coded values.
  command = apply

  assert {
    condition     = aws_bedrockagentcore_agent_runtime.this.environment_variables["MEMORY_ID"] == aws_bedrockagentcore_memory.this.id
    error_message = "MEMORY_ID must be the memory ID."
  }

  assert {
    condition     = aws_bedrockagentcore_agent_runtime.this.environment_variables["GUARDRAIL_ID"] == aws_bedrock_guardrail.this.guardrail_id
    error_message = "GUARDRAIL_ID must be the guardrail ID."
  }

  assert {
    condition     = aws_bedrockagentcore_agent_runtime.this.environment_variables["GUARDRAIL_VERSION"] == aws_bedrock_guardrail_version.this.version
    error_message = "GUARDRAIL_VERSION must be the published version."
  }

  assert {
    condition     = aws_bedrockagentcore_agent_runtime.this.environment_variables["SHEET_SECRET_NAME"] == "reminder-agent/google-service-account"
    error_message = "SHEET_SECRET_NAME must be the secret name."
  }

  assert {
    condition     = aws_bedrockagentcore_agent_runtime.this.environment_variables["AWS_REGION"] == "us-east-1"
    error_message = "AWS_REGION must be the deploy region."
  }
}

run "runtime_name_and_idle_timeout" {
  # Underscored name; idle sessions stop after 5 minutes.
  command = plan

  assert {
    condition     = aws_bedrockagentcore_agent_runtime.this.agent_runtime_name == "reminder_agent_runtime"
    error_message = "Runtime name must be reminder_agent_runtime."
  }

  assert {
    condition     = aws_bedrockagentcore_agent_runtime.this.lifecycle_configuration[0].idle_runtime_session_timeout == 300
    error_message = "Idle timeout must be 300 seconds."
  }
}

run "output_exposes_runtime_arn" {
  # M8 reads the runtime ARN from this output.
  command = apply

  assert {
    condition     = output.agent_runtime_arn == aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
    error_message = "agent_runtime_arn output must equal the runtime ARN."
  }
}
