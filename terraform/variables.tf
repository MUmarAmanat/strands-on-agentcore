# Inputs for the whole deployment. Values that identify a person or an
# account have no default: they come from terraform.tfvars (git-ignored).

variable "aws_region" {
  description = "AWS region to deploy into. Amazon Nova Pro must be available there."
  type        = string
}

variable "project_name" {
  description = "Prefix for resource names. Lowercase letters, digits and hyphens."
  type        = string
  default     = "reminder-agent"

  validation {
    # Short enough for the S3 bucket_prefix limit (37 chars incl. suffix).
    condition     = can(regex("^[a-z][a-z0-9-]{1,19}$", var.project_name))
    error_message = "project_name must be 2-20 chars: lowercase letters, digits, hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Deployment environment, passed to the agent as ENVIRONMENT."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "bedrock_model_id" {
  description = "Bedrock model ID or inference profile ID. First-party Amazon models only."
  type        = string
  default     = "amazon.nova-pro-v1:0"

  validation {
    # Allows "amazon.*" and cross-region profiles like "us.amazon.*".
    # Third-party models are billed through Marketplace (not AWS credits).
    condition     = can(regex("^([a-z]{2,6}\\.)?amazon\\.", var.bedrock_model_id))
    error_message = "bedrock_model_id must be a first-party Amazon model (amazon.* or <geo>.amazon.*)."
  }
}

variable "google_sheet_id" {
  description = "ID of the Google Sheet that holds the tasks."
  type        = string
}

variable "google_sheet_range" {
  description = "A1 range to read from the sheet."
  type        = string
  default     = "Active!A1:Z1000"
}

variable "google_sa_key_path" {
  description = "Local path to the Google service-account JSON key. Stored in Secrets Manager."
  type        = string

  validation {
    # can() also catches a missing file, since file() fails on it.
    condition     = can(jsondecode(file(var.google_sa_key_path)))
    error_message = "google_sa_key_path must point to an existing JSON key file."
  }
}

variable "ses_sender" {
  description = "Email address the reminders are sent from (verified in SES)."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.ses_sender))
    error_message = "ses_sender must be a valid email address."
  }
}

variable "ses_recipient" {
  description = "Email address that receives the reminders (verified in SES while in sandbox)."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.ses_recipient))
    error_message = "ses_recipient must be a valid email address."
  }
}

variable "timezone" {
  description = "IANA timezone that decides \"today\" for the agent and the schedule."
  type        = string
  default     = "Asia/Dubai"
}

variable "llm_timeout_seconds" {
  description = "Hard timeout for a single LLM call."
  type        = number
  default     = 30
}

variable "max_retries" {
  description = "Maximum retry attempts for transient failures."
  type        = number
  default     = 3
}

variable "schedule_expression" {
  description = "When the agent runs, in the timezone above. Default: daily at 16:00."
  type        = string
  default     = "cron(0 16 * * ? *)"

  validation {
    condition     = can(regex("^(cron|rate|at)\\(.+\\)$", var.schedule_expression))
    error_message = "schedule_expression must be cron(...), rate(...) or at(...)."
  }
}

variable "schedule_enabled" {
  description = "Set to false to pause the daily run without destroying anything."
  type        = bool
  default     = true
}

variable "enable_transaction_search" {
  description = "Send X-Ray spans to CloudWatch Logs (needed for AgentCore traces). Account-wide; false if already set up elsewhere."
  type        = bool
  default     = true
}

variable "alert_email" {
  description = "Email address for failure alerts. Empty means ses_recipient."
  type        = string
  default     = ""

  validation {
    condition     = var.alert_email == "" || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be empty or a valid email address."
  }
}

variable "enable_online_evaluation" {
  description = "Score every agent run with AgentCore built-in evaluators (small per-run cost)."
  type        = bool
  default     = true
}

variable "evaluators" {
  description = "Built-in AgentCore evaluator IDs used by online evaluation."
  type        = list(string)
  default = [
    "Builtin.GoalSuccessRate",
    "Builtin.InstructionFollowing",
    "Builtin.Faithfulness",
    "Builtin.ToolSelectionAccuracy",
    "Builtin.ToolParameterAccuracy",
  ]

  validation {
    condition     = length(var.evaluators) >= 1 && length(var.evaluators) <= 10
    error_message = "evaluators must list 1 to 10 evaluator IDs."
  }

  validation {
    condition     = alltrue([for id in var.evaluators : startswith(id, "Builtin.")])
    error_message = "evaluators must be built-in IDs (Builtin.*)."
  }
}
