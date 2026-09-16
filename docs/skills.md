# Skills

## Concept

A skill is a versioned, reviewable unit of instruction: a `SKILL.md` file
with frontmatter and a body. It is the answer to prompts that live as string
literals scattered through a codebase, unversioned and untested.

Skills support progressive disclosure. The agent sees a short summary of
each available skill and loads the full instructions only when it decides
the skill applies. A dozen skills therefore cost a dozen lines of context,
not a dozen documents.

Loading emits `SKILL_LOADED`, and the skill's version and fingerprint go
into the run manifest. When the answer changes after a policy edit, drift
detection shows the skill fingerprint moved.

## Minimal example

```markdown
---
name: refund-policy
description: How to decide whether a refund is allowed.
version: 3
allowed_tools: [issue_refund]
---

# Refund policy

Refund in full within 30 days of purchase. Between 30 and 90 days, refund
the unused portion only. Past 90 days, decline and offer account credit.

Never promise a refund before checking the purchase date.
```

```python
from rewyn import Agent

agent = Agent(model="openai:gpt-5", skills=["./skills/refund-policy"])
```

## Production example

```python
from rewyn.skills import discover_skills, load_skill

agent = Agent(
    model="anthropic:claude-opus-5",
    skills=discover_skills(["./skills"]),  # every SKILL.md under a directory
    skill_mode="progressive",  # summaries now, full text on demand
)

policy = load_skill("./skills/refund-policy")
print(policy.version, policy.fingerprint())
```

`skill_mode="eager"` puts every skill's full text in the context instead.
Use it when you have one or two short skills and want determinism over
token efficiency.

```bash
rewyn skills                       # discovered skills
rewyn skills show refund-policy    # metadata and full instructions
```

## API reference

`rewyn/skills/skill.py` for `Skill`.
`rewyn/skills/loader.py` for `load_skill` and `parse_skill_markdown`.
`rewyn/skills/discovery.py` for `discover_skills`.
`rewyn/skills/lifecycle.py` for `SkillManager` and disclosure modes.

## Failure modes

**`SkillLoadError`.** The frontmatter is missing or malformed. `name` and
`description` are required; `version` defaults to `1`.

**The agent never loads the skill.** In progressive mode the model decides,
based only on the `description`. Write it as a trigger condition ("how to
decide whether a refund is allowed"), not as a title ("refunds").

**The skill is ignored once loaded.** Skills are instructions, not
constraints. If something must not happen, enforce it with a guardrail or a
permission policy as well.

**Changing a skill silently changed behaviour.** That is what `version` and
the fingerprint are for. Bump the version, and compare behavior manifests
across releases. See [Regression](regression.md).
