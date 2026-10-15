# Staging API cutover validation

This operator-run check observes a coordinated staging API switch. It does not deploy, change cloud
settings, refresh authentication, write a manuscript, or invoke a model. Keep
the existing Git integration as the only deployment producer.

The current API volume prevents overlapping deployments. Railway documents a
brief outage for volume-backed services even with a healthcheck configured:
[volume limitations](https://docs.railway.com/volumes/reference). Keep the
300-second Railway drain, 295-second Uvicorn shutdown budget, mounted volume
and original bytes. A successful check measures the observed behavior; it
cannot establish a platform guarantee of uninterrupted sessions.

## Before the deployment

1. Bind the candidate to its exact main SHA and successful main CI/E2E runs.
   Read back the actual API deployment ID, source, branch, readiness path and
   drain settings. A frontend-only main push may legitimately skip the API.
2. Use only a staging synthetic account, project and a stable file outside the
   AI test's write set. Save that file through the normal UI and independently
   read back its UTF-8 content SHA-256. Retain the version token for any later
   intentional write; this probe never writes.
3. Prepare the following JSON in the launcher's process memory and send it to
   stdin. Keep passwords and tokens out of shell arguments, command history,
   Git, screenshots and receipts. The token must remain valid for the bounded
   observation; the probe records a 401 and does not log in or refresh it.

   ```json
   {
     "accessToken": "<synthetic staging access token>",
     "userId": "<synthetic user UUID>",
     "projectId": "<owned project UUID>",
     "fileId": "<stable file UUID>",
     "contentSha256": "<64 lowercase hex characters>"
   }
   ```

4. Start the probe before the coordinated native deployment. The launcher
   supplies the JSON on stdin; the command itself contains no credential:

   ```sh
   node scripts/ci/staging-cutover-probe.mjs \
     --source-sha <40-character-candidate-main-SHA> \
     --output /private/new-empty-cutover-receipt-directory \
     --duration-seconds 600 --interval-seconds 2
   ```

The origin is fixed to `staging-api-staging-a289.up.railway.app`. Each cycle
issues one GET each for the current user, owned project, file tree and stable
file. Responses are checked for the expected identity, ownership and file
content. It records only timestamps, status, latency, request ID and content
hash. Every sample is retained; a later successful read does not erase a 502,
timeout, authentication failure or changed file. No generation or write POST is
retried. Set a long enough bounded observation to include at least a minute
before and after the actual switch; repeat only if the window did not cover it.

## During and after the deployment

Record the old and new deployment IDs and actual provider/lifecycle timestamps
alongside the probe. Preserve the authenticated HTTP request observations, old
shutdown/drain, new startup/readiness and old deployment removal. Correlate any
failed samples with the provider's HTTP request ID and deployment logs; sample
latency or time between failures is not the outage duration of every user.

Separately exercise existing AI SSE through the real staging API using a
deterministic, non-billing provider. Browser route interception cannot establish
backend cancellation or accounting behavior. Coordinate the provider and any
temporary staging configuration with the release operator and restore them
afterwards. Use one run per scenario, never automatic retries:

When the user explicitly authorizes real-model staging validation, a bounded
synthetic request may cover the observed native handoff instead. Record that
authorization, the exact request count, source, actual overlap and ledger cost;
restore temporary configuration afterwards. A naturally completing request
only proves its observed outcome, not a deterministic timeout or the full drain
budget.

- A run that finishes within the drain budget must retain its files, assistant
  terminal state and one account-scoped quota reservation/settlement.
- For an interrupted run, independently read back saved output, stop outcome,
  quota/refund and model ledger after cleanup settles. Existing output remains
  saved. A disconnected run with no substantial output follows the existing
  refund rule; a run with output follows the existing charge rule. An unknown
  provider usage reservation must remain explicitly unresolved, not deleted or
  reported as fully settled.
- Verify the UI leaves the running state and uses the existing recovery path
  after an interruption. Inspect and explicitly decide any continuation; never
  resubmit generation or file writes automatically. A completed server run whose
  final SSE frame was lost must be distinguished from a run that never completed.

Do not shorten drain windows, detach the volume or claim lossless cutover to
make an observation pass. Non-200 reads, unresolved file hashes, missing
terminal/accounting outcomes or missing lifecycle evidence remain failures or
open findings. The read probe always reports `cutoverVerified: false`,
`sseVerified: false` and `providerSourceBinding: NOT_VERIFIED`; only the complete
separate evidence can establish those conclusions. Its exit code is nonzero
if any read sample fails or the operator interrupts it.

## Storage prerequisite for a future volume-free release

`UPLOAD_STORAGE_BACKEND=s3` controls new upload writes; it does not prove every
historical reference has migrated. Inventory both `Novel.source_meta.file_path`
and `IngestionJob.source_path`, plus feedback screenshot references and every
other retained volume consumer. Reuse the existing
[upload migration](../../apps/server/docs/upload-storage-bridge.md): dry run,
skip active jobs, copy and verify private GET bytes/hash, journal before each
reference commit, preserve legacy reads and originals, and rehearse conditional
restore with synthetic staging data. Volume detachment needs a separate
authorization after all consumers are verified. It is outside this check.
