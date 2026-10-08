# Private upload storage bridge

The API supports `UPLOAD_STORAGE_BACKEND=local` (default) and `s3`. S3 mode is
strict: all variables below are required and invalid configuration fails the
request instead of falling back to local disk.

- `UPLOAD_S3_ENDPOINT`
- `UPLOAD_S3_REGION`
- `UPLOAD_S3_BUCKET`
- `UPLOAD_S3_ACCESS_KEY_ID`
- `UPLOAD_S3_SECRET_ACCESS_KEY`
- `UPLOAD_S3_URL_STYLE` (`virtual` or `path`)

Material sources are stored as `s3://<bucket>/material/<user>/<opaque-name>` and
feedback images as `s3://<bucket>/feedback/<opaque-name>`. Only the API holds
bucket credentials. Workers fetch material bytes through the existing internal
token route into a per-flow temporary file, which is removed on success or
failure. Screenshot downloads remain superuser-only. Legacy local references
remain readable beneath their configured trusted roots.

`python scripts/migrate_upload_storage.py --manifest /private/path.json` is a
dry run. Add `--apply` only with a complete S3 configuration; add `--user-id`
to restrict a staging batch. Apply verifies a private GET by size and SHA-256,
writes and fsyncs a mode-0600 restoration entry before each DB commit, preserves
all local files, and skips running ingestion jobs and unsafe/symlinked paths.
Use `--restore` with the same manifest to switch unchanged migrated references
back after revalidating the retained local file. Run migration during a window
without material uploads or retries.

This bridge does not detach the existing volume. Volume removal requires a
separate inventory proving that no remaining runtime path depends on it.
