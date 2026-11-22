# Google service-account key in Secrets Manager. The agent reads it at run
# time by name (SHEET_SECRET_NAME). Note: the value is also kept in the
# Terraform state, which is local and git-ignored.

resource "aws_secretsmanager_secret" "google_sa" {
  name        = "${var.project_name}/google-service-account"
  description = "Google service-account JSON key for reading the task sheet."

  # Delete at once on destroy, so a re-apply can reuse the same name.
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "google_sa" {
  secret_id     = aws_secretsmanager_secret.google_sa.id
  secret_string = file(var.google_sa_key_path)
}
