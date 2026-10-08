# CLAUDE.md

Read [AGENTS.md](AGENTS.md) first. It is the shared project contract for working-tree safety, development commands, staging/production releases, three-day checks, validation and decision notes.

Additional sources of truth:

- [DESIGN.md](DESIGN.md): UI and product design decisions.
- [apps/server/agent/CLAUDE.md](apps/server/agent/CLAUDE.md): agent orchestration, tools and SSE architecture.
- [docs/ops/release-ci-cd.md](docs/ops/release-ci-cd.md): CI, provider gates, release promotion and impact on active users.
- [.agents/notes/](.agents/notes/): implemented and proposed decisions; search before non-trivial changes.

Follow the user's latest task boundaries. The current draft-recovery/upload-storage batch is authorized for staging validation only; production promotion needs a separately authorized release. Recurring three-day checks only update GEO records and report conclusions, without deploying.
