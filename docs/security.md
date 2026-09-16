# Security

## Concept

Two problems, often confused.

**Secrets must never be persisted.** Rewyn writes events to disk and,
optionally, uploads them. Anything that reaches that path is a leak
waiting to happen, so redaction runs on everything before it is written.

**Untrusted content must not be able to set policy.** A retrieved document
or a customer email is data. The moment it can issue instructions the model
obeys, you have prompt injection. Trust levels, permissions and approvals
are three layers against that, and none of them is sufficient alone.

## Secrets

Every event and manifest passes through a `Redactor` before it is written or
uploaded. Three mechanisms cooperate: credential-looking key names
(`api_key`, `authorization`, `password`), known credential formats (OpenAI,
Anthropic, AWS, GitHub, Slack, Google, JWTs, PEM blocks), and exact values
registered at runtime.

```python
from rewyn.security.redaction import default_redactor

default_redactor().register_secret(os.environ["INTERNAL_TOKEN"])
```

Model adapters register their own keys automatically, and so does
`rewyn login`, so a cloud API key cannot appear in a recorded event.

PII patterns exist but are off by default, because whether an email address
is sensitive is application policy, not a safety floor:

```python
from rewyn.security.redaction import Redactor

recorder = Recorder(redactor=Redactor(redact_pii=True))
```

`REWYN_REDACTION=0` disables redaction entirely. It is an explicit
opt-out, and there is no good reason to set it.

## Prompt injection

Defence in depth, in the order that matters:

**1. Mark untrusted content.** Set `trust_level` on context items and on
retrieval pipelines. Untrusted content is rendered inside an explicit
boundary with a notice, and it cannot outrank policy in the ordering.

```python
from rewyn.context import TrustLevel, text_item

context.add(text_item(inbound_email, source="email", trust_level=TrustLevel.UNTRUSTED))
```

**2. Detect the obvious attempts.**

```python
from rewyn.guardrails import PromptInjectionGuardrail

agent = Agent(model=..., guardrails=[PromptInjectionGuardrail(stage="input")])
```

Pattern-based, so it catches common shapes and not a determined attacker.

**3. Make the dangerous action need a human.** This is the layer that
actually holds. If a successful injection still cannot issue a refund
without an approval, it has not achieved much.

```python
from rewyn.tools import MaxRiskLevel, RiskLevel

agent = Agent(
    model=...,
    tools=[issue_refund],  # risk_level="high"
    permission_policy=MaxRiskLevel(approve_above=RiskLevel.MEDIUM),
    approval_handler=QueueHandler(),
)
```

**4. Verify afterwards.** Provenance records what each item was and where it
came from, and re-hashes content on demand:

```python
from rewyn.context import provenance_report

for record in provenance_report(assembled.items):
    print(record["source"], record["trust_level"], record["verified"])
```

## Tenancy

Memory namespaces, cloud projects and API keys are the isolation boundaries,
and each is enforced rather than advisory.

```python
memory = Memory(namespace=f"tenant:{tenant_id}", store=FileStore())
```

Items are stamped on write; reads, deletes and clears refuse to cross the
boundary. In the cloud, every route below the project level is scoped to the
calling key's project, and keys carry a role so a CI key that uploads runs
cannot delete them. See [Cloud](cloud.md).

## API reference

`rewyn/security/redaction.py` for `Redactor`, `default_redactor` and
`redact`.
`rewyn/context/source.py` for `TrustLevel`, `Sensitivity` and authority.
`rewyn/guardrails/validators.py` for `PromptInjectionGuardrail` and
`PIIGuardrail`.
`rewyn/tools/permissions.py` for the permission policies.

## Failure modes

**A secret appears in a recorded event.** It did not match a known pattern
and was not registered. Register it explicitly with `register_secret`.

**Redaction changed a legitimate value.** Pattern matching is not exact.
Check whether the value genuinely looks like a credential before working
around it.

**An untrusted document changed the answer anyway.** Trust levels lower
priority and make the content visible as untrusted; they are not a
guarantee. Layer three and four above are what stop consequences.

**A tool ran without approval.** Approval is driven by the permission
policy, not by the tool's `risk_level` alone. `MaxRiskLevel` is what turns a
high-risk tool into an approval request.

**PII reaches the cloud.** PII redaction is off by default. Turn it on
before syncing runs from a system that handles personal data.

**A leaked key can do more than its job.** Mint keys with the narrowest role
that works: `rewyn-cloud` keys carry `viewer`, `member`, `admin` or
`owner`, and a key cannot mint one more powerful than itself.
