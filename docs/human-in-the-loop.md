# Human in the loop

## Concept

Some actions should not happen without a person: a refund above a threshold,
a production deploy, a credit limit change. The engineering question is not
whether to ask, it is how to ask without the approval becoming an untracked
Slack message.

`approve()` makes the request part of the run.
`HUMAN_APPROVAL_REQUESTED` records what was asked and why;
`HUMAN_APPROVED` or `HUMAN_REJECTED` records who decided and on what
grounds. Replay a run six months later and the approval is in it.

## Minimal example

```python
from rewyn.human import approve

decision = await approve("issue refund", risk="high", amount_cents=25_000)
if decision.approved:
    issue_refund(...)
```

Without a configured handler the default rejects. Failing closed is the
right default for an approval gate.

## Production example

Approvals gate tool calls through the permission policy. Note
`approve_above` rather than the first positional argument: that one is a
ceiling above which calls are refused outright, with nobody asked.

```python
from rewyn.human import QueueHandler
from rewyn.tools import MaxRiskLevel, RiskLevel

handler = QueueHandler(timeout=300.0)

agent = Agent(
    model=...,
    tools=[issue_refund],  # risk_level="high"
    permission_policy=MaxRiskLevel(approve_above=RiskLevel.MEDIUM),
    approval_handler=handler,
)

# Elsewhere: your web app resolves the request by id when a reviewer decides.
handler.resolve(request_id, approved=True, by=reviewer.email, reason="within policy")
```

Handlers: `AutoApprove` and `AutoReject` for tests, `ConsoleHandler` for a
terminal, `CallbackHandler` to call your own function, `QueueHandler` for a
web or Slack flow.

```python
from rewyn.human import AutoApprove, approval_scope

with approval_scope(AutoApprove(by="ci")):  # scoped, not global
    await graph.arun(question)
```

### Feedback and corrections

```python
from rewyn.human import correct, escalate, record_feedback

await record_feedback(rating=2, comment="Missed the contract renewal", by=reviewer.email)
await correct(edited_text, target=result.run_id, by=reviewer.email)
await escalate("policy exception requested", to="credit-committee", by="credit-analyst")
```

Corrections are the raw material for a dataset: a corrected run is a golden
example of what the answer should have been. See [Regression](regression.md).

## API reference

`rewyn/human/approval.py` for `approve`, `ApprovalRequest`,
`ApprovalDecision`, the handlers and `approval_scope`.
`rewyn/human/feedback.py` for `record_feedback`, `correct` and `escalate`.

## Failure modes

**Everything is rejected.** No handler is configured, so the default
`AutoReject` is in force. That is deliberate; configure one.

**The run hangs.** `QueueHandler` waits for a decision. Set `timeout`, and
decide what an unanswered request means: rejecting is usually safer than
proceeding.

**`AutoApprove` reaches production.** Use `approval_scope` so it is scoped
to tests rather than set globally with `set_default_handler`.

**The approver had no context.** Pass details: `approve("raise credit
limit", account=..., proposed_limit=..., ceiling=...)`. They are recorded
and shown to the handler.

**An approval is recorded but nothing was gated.** `approve()` returns a
decision; it does not stop anything by itself. Branch on it.
