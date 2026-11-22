# Bedrock Guardrail: checks the model's input/output and, via ApplyGuardrail,
# the sheet text inside fetch_tasks (prompt injection, harmful content).

locals {
  # type => [input_strength, output_strength]
  guardrail_filters = {
    # Prompt attacks only apply to input; the API requires output NONE.
    PROMPT_ATTACK = ["HIGH", "NONE"]
    # MEDIUM, not HIGH, so harmless tasks ("kill the stale process") pass.
    HATE       = ["MEDIUM", "MEDIUM"]
    INSULTS    = ["MEDIUM", "MEDIUM"]
    SEXUAL     = ["MEDIUM", "MEDIUM"]
    VIOLENCE   = ["MEDIUM", "MEDIUM"]
    MISCONDUCT = ["MEDIUM", "MEDIUM"]
  }
}

resource "aws_bedrock_guardrail" "this" {
  name                      = "${var.project_name}-guardrail"
  description               = "Blocks prompt injection and harmful content in task text."
  blocked_input_messaging   = "Blocked by guardrail: the input was not allowed."
  blocked_outputs_messaging = "Blocked by guardrail: the response was not allowed."

  content_policy_config {
    dynamic "filters_config" {
      for_each = local.guardrail_filters

      content {
        type            = filters_config.key
        input_strength  = filters_config.value[0]
        output_strength = filters_config.value[1]
      }
    }
  }
}

# The agent uses a fixed version, not DRAFT, so a run never sees half-edited
# settings. Any guardrail change publishes a new version.
resource "aws_bedrock_guardrail_version" "this" {
  guardrail_arn = aws_bedrock_guardrail.this.guardrail_arn
  description   = "Managed by Terraform."

  lifecycle {
    replace_triggered_by = [aws_bedrock_guardrail.this]
  }
}
