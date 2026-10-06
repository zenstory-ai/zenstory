# ZenStory CLI changelog

## [Unreleased]

Candidate: 0.2.0

The existing `0.2.0` package source is a release candidate. It has not been
published or assigned a release date. Before creating `cli-v0.2.0`, move these
notes under `## [0.2.0] - YYYY-MM-DD` and leave a new Unreleased section.

### Added

- Public Agent API commands and the bundled ZenStory Agent Skill.

### Fixed

- Enforce `files put --if-updated-at` atomically on the server, preserving the
  full timestamp instead of relying only on a client-side preflight.
- Compare naive-UTC timestamps without local timezone or microsecond loss and
  report server stale-write races with the same `STALE_WRITE` code as preflight.
- Avoid downloading full file content for metadata-only updates.
- Reject API bases containing credentials, query strings or fragments without
  echoing embedded credentials in validation errors.
