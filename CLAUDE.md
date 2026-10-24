# CLAUDE.md

@AGENTS.md

AGENTS.md (imported above) is the shared project contract for working-tree safety, development commands, staging/production releases, three-day checks, validation and decision notes.

For Agent semantic decisions, follow [the shared rule](AGENTS.md#agent-semantic-decisions): do not use keywords, phrase/substring matching, regex, or length/punctuation heuristics to infer intent, continuation, consent, scope, authority, or completion. Use validated structured decisions and actual state/evidence. Bounded protocol parsing and content/concurrency protection remain required; this rule does not authorize removing existing guards.

Additional sources of truth:

- [DESIGN.md](DESIGN.md): UI and product design decisions.
- [apps/server/agent/AGENTS.md](apps/server/agent/AGENTS.md): agent orchestration, tools and SSE architecture (`apps/server/agent/CLAUDE.md` imports it).
- [docs/ops/release-ci-cd.md](docs/ops/release-ci-cd.md): CI, provider gates, release promotion and impact on active users.
- [.agents/notes/](.agents/notes/): implemented and proposed decisions; search before non-trivial changes.
