# Cloud

## Concept

The SDK is complete without this. `pip install rewyn` records, replays,
evaluates and gates with no account, no key and no network. That is the
boundary spec §47 draws, and it is deliberate: the open-source half has to
be genuinely useful on its own.

The cloud adds what a team needs and a laptop cannot provide: shared runs,
search across everyone's work, centralised datasets, and evaluation history
that outlives one developer's `.rewyn/` directory.

## Where it lives

The service itself is not part of this repository. This package is the client
half: `rewyn login`, `rewyn sync` and the remote store below are open source
and fully specified, so the protocol a deployment speaks is inspectable even
though the server is commercial. See <https://rewyn.dev> for the hosted
service.

Nothing on this page is required. The SDK records, replays, evaluates and
gates with no account and no network, and it always will — that boundary is
the point, not a limitation to be removed later.

## Connecting the SDK

```bash
rewyn login --endpoint https://api.rewyn.dev --key rw_...
rewyn sync                        # upload local runs
rewyn sync --datasets --evaluations
rewyn sync --status               # what is configured and what is queued
rewyn sync --pull run_01J...      # fetch a run to replay locally
rewyn logout
```

Credentials are written to `.rewyn/credentials.json` with owner-only
permissions, and the key is registered with the redactor so it cannot appear
in a recorded event. `REWYN_API_KEY` and `REWYN_ENDPOINT` override the
file.

## From code

```python
from rewyn.storage.remote import RemoteStore

store = RemoteStore()

await store.apush_runs()  # every local run
await store.apush_dataset(dataset)
await store.apush_report(report)

listing = await store.alist_runs(q="refund", status="failed", limit=20)
await store.apull_run(listing["runs"][0]["run_id"])  # then replay it locally
```

Sync never raises into your application. Failures are collected into a
`SyncResult`, and the run ids are queued locally and retried on the next
sync, so a laptop that was offline catches up rather than losing runs. Pass
`strict=True` when you do want the exception.

```python
result = await store.apush_runs()
print(result.summary())  # uploaded, replaced, failed, queued, ok
```

## Teams, roles and governance

Four roles, ordered, so each can do everything the one below it can.

| Role | Can |
| --- | --- |
| `viewer` | read runs, datasets and evaluations |
| `member` | also upload runs and write datasets |
| `admin` | also manage keys, alerts and retention, and delete runs |
| `owner` | also manage the team and its members |

Keys carry a role, so a CI key that uploads runs cannot delete them, and a
key can never mint one more powerful than itself. The blast radius of a
leaked key should be the job it was minted for.

```bash
curl -X POST "$API/v1/keys?label=ci&role=member" -H "Authorization: Bearer $OWNER_KEY"
```

Teams own projects and members. SSO maps a verified assertion to a team by
email domain and provisions the member on first sight; whatever terminates
SSO in front of the service is what verifies the identity.

```bash
curl -X POST "$API/v1/teams" -d '{"name":"Acme","sso_domain":"acme.com"}' ...
curl -X POST "$API/v1/team/members" -d '{"email":"dana@acme.com","role":"admin"}' ...
curl -X POST "$API/v1/sso/login" -d '{"email":"dana@acme.com","provider":"okta"}'
```

Access changes and deletions are audited. The log is scoped to your project
or your team, and readable by an admin:

```bash
curl "$API/v1/audit?action=key.create" -H "Authorization: Bearer $KEY"
```

Retention is a per-team window, applied explicitly. Nothing deletes data on
a timer inside the service; call it from a scheduled job you can see and
stop.

```bash
curl -X PUT "$API/v1/team/retention" -d '{"days": 90}' ...
curl -X POST "$API/v1/retention/apply" ...
```

## Shared incidents, comments and views

The cloud console serves the same screens as `rewyn ui`, over the same
contract, with one difference that only a team has: the things people write
are shared.

An incident is detected from the project's runs on every request, so it needs
no storage. What is stored is the human half -- who took it, what status it
is in, and the trail of both -- alongside comment threads and saved views.
All three are scoped to the project, readable by any `viewer` and writable by
`member` and above.

```bash
curl "$API/console/v1/incidents" -H "Authorization: Bearer $KEY"
curl -X POST "$API/console/v1/incidents/errors-2d711642b7" \
  -d '{"status":"investigating","assignee":"dana@acme.com"}' ...
curl -X POST "$API/console/v1/comments" \
  -d '{"subject":"run:run_01J...","body":"this is the slow one"}' ...
curl "$API/console/v1/views?screen=runs" -H "Authorization: Bearer $KEY"
```

A comment's subject must be something the console can open -- a run, an
incident, a dataset, an agent, a release or a saved view -- so a thread
cannot be orphaned by a typo. The author is the key's label, which is what
identity means on a surface authenticated by keys.

## Alerts and dashboards

Thresholds on `error_rate`, `cost_per_run`, `total_cost`, `avg_latency_ms`
or `run_count` over a window. Evaluation is pull-based: a push notifier
would put an outbound network call on the ingest path, which is where the
performance requirements say nothing slow belongs.

```bash
curl -X POST "$API/v1/alerts" \
  -d '{"name":"errors","metric":"error_rate","threshold":0.05,"window_minutes":60}' ...
curl -X POST "$API/v1/alerts:evaluate" ...     # returns the ones that fired
curl "$API/v1/stats?window_hours=24" ...       # the figures a dashboard renders
```

`/v1/stats` returns data rather than a rendering, so a web UI, the CLI and
an alerting job all read the same numbers.

## Architecture

```
SDK -> API -> service layer -> SQLAlchemy -> PostgreSQL
                            -> object store (event logs)
```

Run manifests are rows, so they can be searched and aggregated. Event logs
are blobs behind a two-method object-store protocol, so a large run does not
bloat the database and S3 replaces the filesystem backend without touching
the service:

```python
from rewyn.integrations.s3 import S3ObjectStore

create_app(store=S3ObjectStore("rewyn-runs", prefix="prod/"))
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | SQLite under the home | PostgreSQL in production |
| `REWYN_CLOUD_HOME` | `./.rewyn-cloud` | Database and object storage root |
| `REWYN_CLOUD_STORAGE` | `<home>/objects` | Object storage directory |
| `REWYN_CLOUD_OPEN_REGISTRATION` | off | Let anyone create a project |

API keys are stored only as SHA-256 hashes with a short non-secret prefix.
Every route below the project level is scoped to the calling key's project.

## API reference

`rewyn/storage/remote.py` for `RemoteStore`, `Credentials`, `SyncResult`
and `SyncQueue` — the whole client half of the protocol.
`rewyn/ui/schemas.py` for the wire types the console reads, which the
service returns unchanged.

## Failure modes

**`NotAuthenticatedError`.** No key configured, or it was revoked. Run
`rewyn login`. `rewyn sync --status` shows what is configured without
printing the key.

**Syncing needs httpx.** `pip install "rewyn[remote]"`.

**Uploads silently do nothing.** Check `rewyn sync --status` for a queue.
Failed uploads are queued, not lost, and retried on the next sync.

**A pulled run will not replay.** Pull fetches the manifest and the events
into local storage; replay reads from there. If the run was ingested without
its events, there is nothing to substitute.

**Timestamps look wrong.** SQLite has no timezone type. The API reattaches
UTC on the way out, so treat anything naive from a direct database read as
UTC.

**A request returns 403.** The key's role is below what the operation needs.
The message says which role would be enough.

**One project sees another's runs.** It cannot; every query is scoped by the
key's project. If you are seeing cross-tenant data, you are using one key for
two tenants. Use one project per tenant.
