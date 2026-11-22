# Reminder Agent on AWS

A daily AI agent that reads your tasks from a Google Sheet and emails you one reminder,
grouped by priority. It runs on AWS-native agent infrastructure: **Strands Agents** on
**Amazon Bedrock AgentCore Runtime**, triggered by **EventBridge Scheduler**, with no
Docker and no Lambda. One `terraform apply` builds the code package and deploys
everything.

```
EventBridge Scheduler (daily cron, your timezone)
  -> AgentCore Runtime (InvokeAgentRuntime, payload {})
     -> Strands agent (Amazon Nova Pro on Bedrock, IAM auth)
        -> tool fetch_tasks          Google Sheets (key from Secrets Manager),
                                     sheet text checked by a Bedrock Guardrail
        -> tool send_reminder_email  Amazon SES, at most once per date
                                     (AgentCore Memory records sent dates)
  -> traces, alarms and online evaluation in CloudWatch
```

## Contents
- [What gets deployed](#what-gets-deployed)
- [Prerequisites](#prerequisites)
- [Google Sheet format](#google-sheet-format)
- [Deploy](#deploy)
- [Configuration](#configuration)
- [Test a run manually](#test-a-run-manually)
- [Observability, alerts and evaluation](#observability-alerts-and-evaluation)
- [Local development](#local-development)
- [Cost](#cost)
- [Limitations](#limitations)
- [Cleanup](#cleanup)
- [Project layout](#project-layout)
- [License](#license)

## What gets deployed

| Service | Purpose |
|---|---|
| AgentCore Runtime | Runs the agent from a zip in S3 (direct code deploy, Python 3.12, ARM64) |
| Amazon Bedrock (Nova Pro) | The LLM. First-party model, IAM auth, no API key |
| Bedrock Guardrail | Blocks prompt injection and harmful content in the sheet text and model I/O |
| AgentCore Memory | Records dates that already got a reminder, so retries never send twice |
| EventBridge Scheduler | Calls `InvokeAgentRuntime` once a day (universal target, no Lambda) |
| Amazon SES | Sends the reminder email |
| Secrets Manager | Holds the Google service-account key |
| S3 | Holds the agent zip |
| CloudWatch + X-Ray | Traces (Transaction Search), failure alarms, evaluation scores |
| SNS | Emails you when a run fails |
| AgentCore Evaluations | Scores every run with built-in LLM-as-a-judge evaluators |

## Prerequisites

Terraform cannot do these steps for you.

**AWS**
- An AWS account and CLI credentials for a deploy user (see [Deployer IAM policy](#deployer-iam-policy)).
- A region where AgentCore Runtime, Memory and Evaluations, and **Amazon Nova Pro** are
  available. Tested in `us-east-1`.

**Tools**
- [Terraform](https://developer.hashicorp.com/terraform/install) **1.7 or later**. The AWS
  provider is pinned by `terraform/.terraform.lock.hcl` (6.73).
- [uv](https://docs.astral.sh/uv/) with **Python 3.12**. Terraform calls uv to build the zip.
- Internet access during `terraform plan`/`apply`: uv downloads the Linux ARM64 wheels.
- **No Docker needed.**

**Google**
- A Google Cloud project with the **Google Sheets API** enabled.
- A **service account** and its **JSON key file**. Terraform stores the key in Secrets
  Manager; keep the file outside the repo.
- Your sheet **shared with the service account's email address** (Viewer is enough).

**After the first `terraform apply`**
- **SES:** click the verification links AWS emails to the sender and recipient addresses.
  While your account is in the SES sandbox, both must be verified, or no email is sent.
- **Alerts:** click the SNS **Confirm subscription** link sent to `alert_email` (or the
  recipient). No failure alert arrives until you do.

### Deployer IAM policy

Terraform runs as an IAM user with a least-privilege policy:
[`docs/deployer-policy.json`](docs/deployer-policy.json).

1. Replace `<ACCOUNT_ID>` and `<REGION>` with your values.
2. IAM → Policies → Create policy → JSON → paste → attach it to your deploy user.

Notes:
- The policy assumes the default `project_name = "reminder-agent"`. If you change it, change
  the `reminder-agent` / `reminder_agent` names in the policy too.
- It is close to the 6,144-character limit for a managed policy.
- Enabling **Transaction Search** (first apply) makes AWS run setup steps with your
  credentials (the `aws/spans` log group, an Application Signals service-linked role and
  CloudTrail channel). The policy includes them. Expect that resource to take up to about
  10 minutes.

## Google Sheet format

The first row is a header. Columns are matched by **case-insensitive substring**, so
`Priority (1 high - 5 low)` works.

| Column (any header containing…) | Meaning | Example |
|---|---|---|
| `name`, `title` or `task` | Task title (rows without one are skipped) | Write report |
| `start date` or `start` | Start date (`YYYY-MM-DD`, `DD/MM/YYYY`, `MM/DD/YYYY`, `DD-MM-YYYY`, `June 22, 2026`, `Jun 22, 2026`) | 2026-11-01 |
| `priority` | 1 (highest) to 5 (lowest); empty = "No priority" | 2 |
| `days passed` or `days` | Days since the start date | 21 |
| `completed`, `complete` or `done` | `yes`/`y`/`true`/`done`/`completed`/`1` = done (excluded) | no |

All five columns must exist. The default range is `Active!A1:Z1000`: a tab named `Active`.

## Deploy

```bash
git clone <this-repo-url>
cd reminder-agent-on-aws/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in your values (git-ignored)
terraform init
terraform apply
```

`terraform apply` builds the zip (`scripts/build_package.py`), uploads it to S3 and creates
all resources. Then do the [after-apply steps](#prerequisites) (SES and SNS emails).

The schedule starts right away: by default the agent runs daily at **16:00 Asia/Dubai**.
Set `schedule_enabled = false` to deploy without it.

**Redeploying code:** change the code and run `terraform apply` again. The zip is
deterministic: the same code gives the same sha256 and nothing changes; new code gives a
new S3 key and a runtime update.

## Configuration

Set in `terraform/terraform.tfvars`. Required values have no default.

| Variable | Default | Description |
|---|---|---|
| `aws_region` | (required) | Region to deploy into |
| `google_sheet_id` | (required) | ID from the sheet URL |
| `google_sa_key_path` | (required) | Local path to the service-account JSON key |
| `ses_sender` | (required) | From address (verified in SES) |
| `ses_recipient` | (required) | To address (verified in SES while in sandbox) |
| `project_name` | `reminder-agent` | Prefix for resource names |
| `environment` | `dev` | `dev`, `staging` or `prod` |
| `bedrock_model_id` | `amazon.nova-pro-v1:0` | First-party Amazon models only (`amazon.*` or `<geo>.amazon.*`) |
| `google_sheet_range` | `Active!A1:Z1000` | A1 range to read |
| `timezone` | `Asia/Dubai` | IANA timezone for "today" and for the schedule |
| `schedule_expression` | `cron(0 16 * * ? *)` | When the agent runs, in `timezone` |
| `schedule_enabled` | `true` | `false` pauses the daily run |
| `llm_timeout_seconds` | `30` | Timeout for one LLM call |
| `max_retries` | `3` | Retries for transient failures |
| `enable_transaction_search` | `true` | Send traces to CloudWatch. Account-wide; set `false` if already enabled |
| `alert_email` | `""` | Failure alert address; empty = `ses_recipient` |
| `enable_online_evaluation` | `true` | Score every run (small per-run cost) |
| `evaluators` | 5 built-ins | `Builtin.GoalSuccessRate`, `InstructionFollowing`, `Faithfulness`, `ToolSelectionAccuracy`, `ToolParameterAccuracy` |

Third-party Bedrock models (for example Anthropic) are rejected on purpose: they are billed
through AWS Marketplace and are not covered by AWS credits.

## Test a run manually

```bash
ARN=$(terraform output -raw agent_runtime_arn)
aws bedrock-agentcore invoke-agent-runtime \
  --agent-runtime-arn "$ARN" \
  --runtime-session-id "$(uuidgen)-manual" \
  --payload '{"reference_date": "2026-01-15"}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout
```

- The response is `{"status": "accepted", ...}` at once; the agent runs in the background.
- `reference_date` is optional (default: today in `timezone`). Use a date that has not been
  sent yet: a date that already got its email is skipped.
- The session ID must be at least 33 characters.

## Observability, alerts and evaluation

**Traces:** CloudWatch → **GenAI Observability** → Bedrock AgentCore → your agent. Each run
shows the model calls, tool calls, Memory, Secrets Manager and Guardrail spans, with tokens
and latency. Traces can take a few minutes to appear.

**Logs:** log group `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`.

**Alerts (SNS email):**
- `reminder-agent-run-failed`: the agent logged `Reminder run failed`.
- `reminder-agent-scheduler-errors`: Scheduler could not invoke the runtime.

Test the email path without breaking anything:
```bash
aws cloudwatch set-alarm-state --alarm-name reminder-agent-run-failed \
  --state-value ALARM --state-reason "Manual test"
```
You get an ALARM email, then an OK email a few minutes later.

**Online evaluation:** every run is scored about 15–20 minutes after it ends (AgentCore
waits for the session to go idle). See the scores in CloudWatch → GenAI Observability →
your agent → **Evaluations** tab (session, trace and span evaluations, each with the judge's
explanation), or in the log group from `terraform output evaluation_results_log_group`.
Metrics go to the `Bedrock-AgentCore/Evaluations` namespace.

## Local development

```bash
uv sync                                   # install deps (incl. dev)
uv run pytest                             # 91 unit tests
cd terraform && terraform test            # 55 Terraform tests (mocked, no AWS calls)
```

Run one real cycle from your machine (needs `.env`, copied from `.env.example`, and AWS
credentials that can call Bedrock, Secrets Manager and SES):

```bash
uv run python -m reminder_agent.main --dry-run          # prints the email, sends nothing
uv run python -m reminder_agent.main --date 2026-01-15  # sends via SES
```

Locally, empty `MEMORY_ID` disables the duplicate check, and empty `GUARDRAIL_ID` /
`GUARDRAIL_VERSION` disable the guardrail.

## Cost

Rough monthly estimate for **one run per day**, on-demand prices. Check the AWS pricing
pages for your region.

| Item | Approx. per month |
|---|---|
| AgentCore Evaluations (5 evaluators, ~7 judge calls per run) | ~$4 |
| Secrets Manager (1 secret) | $0.40 |
| Bedrock Nova Pro (~4.5K tokens per run) | < $0.20 |
| AgentCore Runtime (seconds per run), Memory, Guardrail, SES, Scheduler, S3, SNS | cents |
| CloudWatch logs, spans, 2 alarms | < $1 |
| **Total** | **~$5** |

Evaluation is most of the cost. Set `enable_online_evaluation = false` or use fewer
`evaluators` to cut it.

## Limitations

- **One sheet, one recipient, Google Sheets only.** Notion is in the code's config but not
  wired into Terraform.
- **SES sandbox:** only verified addresses receive mail until you request production access.
- **One email per date.** Memory records each sent date (kept 7 days). Re-running the same
  date, even manually, is skipped. That is by design: Scheduler retries are safe.
- **Fixed session ID for scheduled runs.** Scheduler's universal target does not fill
  `<aws.scheduler.*>` placeholders, so every scheduled run uses the same ID. It is safe
  because the runtime's 5-minute idle timeout ends each session long before the next day.
- **Guardrail on the whole sheet.** The injection check covers all task text in one call:
  one malicious row blocks that day's whole email. Content filters are MEDIUM, so harmless
  phrases pass, but a false block is possible.
- **Tool errors don't fail the run.** Strands passes a tool error (for example, a broken
  sheet) back to the model, so the run ends normally and the failure alarm does not fire.
  The alarm covers crashes only. Check traces or evaluation scores for quality problems.
- **Evaluation scores are LLM judgments,** not ground truth. `InstructionFollowing` grades
  the user request ("run today's reminder check"), not the email format rules in the system
  prompt; checking those needs a custom evaluator (not included). In some regions, built-in
  evaluators use cross-region inference, so prompts may be processed in another region.
- **Transaction Search is account-wide** and stays on after `terraform destroy`.
- **Secrets in Terraform state.** The Google key is stored in the state file in plain text.
  State is local and git-ignored here; for shared use, move it to an encrypted remote
  backend.
- **Package size:** the AgentCore zip limit is 250 MB; this agent is well below it.

## Cleanup

```bash
cd terraform
terraform destroy
```

Then, by hand, if you want them gone:
- **Runtime log group** `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`: AgentCore
  creates it, so Terraform does not delete it. It never expires.
- **Evaluation results log group** `/aws/bedrock-agentcore/evaluations/results/...`.
- **Transaction Search:** CloudWatch → Application Signals → Transaction Search, if no other
  workload in the account uses it.
- **SES identities** are deleted by destroy; verification is needed again on the next apply.

## Project layout

```
src/reminder_agent/   agent code (Strands agent, tools, AgentCore entry point)
scripts/              build_package.py: builds the ARM64 zip for AgentCore
tests/                Python unit tests (pytest)
terraform/            infrastructure (*.tf) and Terraform tests (tests/*.tftest.hcl)
docs/                 deployer IAM policy template
```

## License

[MIT](LICENSE)
