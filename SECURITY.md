# Security Policy

## Supported versions

Rewyn is pre-1.0. Security fixes land on the latest released minor version;
there are no long-term support branches yet.

| Version | Supported |
| --- | --- |
| 0.1.x | yes |
| < 0.1 | no |

## Reporting a vulnerability

**Please do not open a public issue, pull request or discussion.**

Report privately through GitHub:
[**Report a vulnerability**](https://github.com/rewyn-dev/rewyn/security/advisories/new).
This opens a private advisory visible only to you and the maintainers, and it
works even if you have never contributed before.

Please include what you can: the version, what an attacker can do, and the
smallest reproduction you have. A proof of concept is welcome but not
required — a clear description of the flaw is enough to start.

### What to expect

- **Acknowledgement within 72 hours.** If you have not heard back, assume the
  message was lost and try again rather than assuming it was ignored.
- **An assessment within 7 days**, including whether we agree it is a
  vulnerability and a rough severity.
- **Credit in the advisory and the changelog**, unless you would rather stay
  anonymous. Say which you prefer.

We will tell you when a fix ships. If we disagree that a report is a
vulnerability, we will say so plainly and explain why.

## Scope

Rewyn records what agents do, so the security properties that matter most are
about what gets written down and what untrusted content can reach.

In scope, and the areas we care about most:

- **Secret leakage.** API keys, tokens, passwords and private keys must be
  redacted before anything is written to `.rewyn/`, exported, or uploaded.
  A path that persists a credential is a vulnerability, not a bug.
- **Trust-level escalation.** Context items carry `trust_level`, `authority`,
  `sensitivity` and `provenance`. Untrusted retrieved content that can
  override trusted policy — prompt injection that changes what the agent is
  allowed to do — is in scope.
- **Sandbox escape.** Tool execution and container sandboxing escaping their
  declared permissions.
- **Replay and bundle handling.** A malicious run bundle that achieves code
  execution or path traversal when imported.
- **The sync client.** Credential handling, TLS behaviour, and anything that
  sends more than it should.

Out of scope:

- Vulnerabilities in provider SDKs or other dependencies. Report those
  upstream; tell us if Rewyn's usage makes them materially worse.
- An agent producing wrong, offensive or unsafe *output*. That is a model
  behaviour question, and the evaluation and guardrail tooling exists to
  measure it.
- Anything requiring an attacker who already has write access to your
  `.rewyn/` directory or your machine.
- Denial of service through deliberately enormous local inputs.

## Hardening notes

Two defaults worth knowing when you deploy:

- **Redaction is on by default** and can be disabled with `REWYN_REDACTION`.
  Turning it off means secrets reach disk. There is no case where that is
  right in production.
- **Recording writes run content to disk.** Treat `.rewyn/` as sensitive: it
  holds prompts, retrieved documents and tool arguments. Keep it out of
  version control — the default `.gitignore` does this — and off shared
  volumes you would not put application logs on.
