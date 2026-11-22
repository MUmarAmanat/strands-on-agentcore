# SES email identities. AWS emails a verification link to each address;
# already-verified addresses need no new click. In the SES sandbox both
# sender and recipient must be verified.

resource "aws_ses_email_identity" "this" {
  # A set, so the same address for sender and recipient gives one identity.
  for_each = toset([var.ses_sender, var.ses_recipient])

  email = each.value
}
