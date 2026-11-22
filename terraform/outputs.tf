# Values that leave Terraform. Resources in this module reference each
# other directly; outputs are only for humans and CLI commands.

output "agent_runtime_arn" {
  description = "ARN of the AgentCore Runtime, for a manual test invoke (terraform output -raw agent_runtime_arn)."
  value       = aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
}

output "schedule_name" {
  description = "Name of the daily schedule (aws scheduler get-schedule --name ...)."
  value       = aws_scheduler_schedule.daily.name
}

output "alerts_topic_arn" {
  description = "SNS topic that receives failure alarms (add more subscribers here)."
  value       = aws_sns_topic.alerts.arn
}

output "evaluation_results_log_group" {
  description = "Log group with the online evaluation scores (null when disabled)."
  value = try(
    aws_bedrockagentcore_online_evaluation_config.this[0].output_config[0].cloudwatch_config[0].log_group_name,
    null,
  )
}
