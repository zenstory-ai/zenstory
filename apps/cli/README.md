# zenstory CLI

Command-line client for [zenstory](https://zenstory.ai) novel projects, plus a standard
[Agent Skill](https://agentskills.io) that teaches coding agents (Claude Code, Codex,
OpenClaw, ...) how to use it. It talks to the public zenstory Agent API.

- Zero runtime dependencies, Node.js ≥ 20.
- Human-readable output by default, `--json` on every command for agents.

## Install

```bash
npm install -g zenstory      # or run any command with: npx -y zenstory <command>
```

## Log in

Create an Agent API key in **zenstory → Settings → Agent** (keys start with `eg_`; enable
the `write` scope if you want the CLI or an agent to edit files). Then:

```bash
zenstory login                               # paste the key when prompted (hidden input)
printf %s "$KEY" | zenstory login --key -    # non-interactive (CI, scripts)
zenstory login --api-base https://zenstory.example.com/api/v1   # self-hosted server
zenstory whoami                              # API base, masked key, read/write scopes
zenstory logout
```

Don't pass the key itself as `--key eg_...`: it would end up in your shell history. If a
key was ever pasted into an AI chat, regenerate it in Settings → Agent.

## Commands

| Command | API endpoint |
|---|---|
| `projects list` | `GET /agent/projects` |
| `projects get <projectId>` | `GET /agent/projects/{id}` |
| `projects create --name N [--description D] [--type novel\|short\|screenplay] [--lang zh\|en]` | `POST /agent/projects` |
| `projects update <projectId> [--name N] [--description D]` | `PUT /agent/projects/{id}` |
| `projects delete <projectId> --yes` | `DELETE /agent/projects/{id}` |
| `files list <projectId> [--type T] [--parent ID] [--fields csv] [--limit N] [--offset N] [--all]` | `GET /agent/projects/{id}/files` |
| `files tree <projectId>` | `GET /agent/projects/{id}/files` (all pages) |
| `files get <fileId> [-o path [--force]] [--fields csv]` | `GET /agent/files/{id}` |
| `files create <projectId> --title T [--type T] [--parent ID] [--order N] [--content-file P \| --content -] [--metadata JSON]` | `POST /agent/projects/{id}/files` |
| `files put <fileId> [--content-file P \| --content -] [--title T] [--order N] [--if-updated-at TS] [--allow-shrink]` | `PUT /agent/files/{id}`; preceding `GET` for content backup or a timestamp preflight |
| `files move <fileId> --parent <folderId\|root> [--order N]` | `POST /agent/files/{id}/move` |
| `files delete <fileId> --yes` | `DELETE /agent/files/{id}` |
| `files versions <fileId> [--limit N] [--offset N] [--include-auto-save]` | `GET /agent/files/{id}/versions` |
| `files version <fileId> <n> [-o path [--force]]` | `GET /agent/files/{id}/versions/{n}` |
| `files rollback <fileId> <n> --yes` | `POST /agent/files/{id}/versions/{n}/rollback` |
| `search <projectId> <query> [--limit N] [--type T] [--content]` | `POST /agent/projects/{id}/search` |
| `context <projectId> [--file ID] [--query Q] [--max-items N]` | `GET /agent/projects/{id}/writing-context` |
| `skill install [--target ...] [--force]`, `skill path` | – |

File types: `outline`, `draft`, `character`, `lore`, `snippet` (materials; `material` is
accepted as an alias), `script`, `document`, `folder`.

`projects create` sets up the same default folders as the web app (e.g.
`<projectId>-draft-folder`, `<projectId>-character-folder`) and prints them; `--lang`
picks the folder-title language (default from `ZENSTORY_LANG`, `LC_ALL` or `LANG`). Keys
limited to specific projects cannot create projects (403). A file's
`--parent` must be a folder of the same project; `--order` sets its position among its
siblings (draft/outline/script files titled `第N章` / `Chapter N` always sort by N).

Run `zenstory --help` or `zenstory <command> --help` for details. Full reference:
[`skill/zenstory/references/cli.md`](skill/zenstory/references/cli.md).

Example — edit a chapter:

```bash
zenstory files list $PROJECT --type draft
zenstory files get $CHAPTER --fields updated_at --json   # note updated_at
zenstory files get $CHAPTER -o chapter.md
$EDITOR chapter.md
zenstory files put $CHAPTER --content-file chapter.md --if-updated-at "$UPDATED_AT"
```

`--if-updated-at` sends the original timestamp to the server for a locked check;
if the current token differs, the server rejects the write with 409. Both local
and server stale-write errors use `STALE_WRITE`. Re-read and merge before retrying;
naive timestamps are UTC, and microseconds must not be rounded.

`files put` refuses to shrink a file below half its size without `--allow-shrink`. Every
content change is kept as a server-side version (unless the plan's per-file version quota
is full — then the CLI warns and only the local backup remains), so a bad edit can be undone:

```bash
zenstory files versions $CHAPTER                 # newest first
zenstory files version $CHAPTER 3 -o v3.md       # inspect version 3
zenstory files rollback $CHAPTER 3 --yes         # restore it (recorded as a new version)
```

As a second net, `files put` also saves the previous server content to
`~/.cache/zenstory/backups/` (respects `XDG_CACHE_HOME`, mode 0600) before writing.

## Agent Skill

The package ships a skill at `skill/zenstory/` (`SKILL.md` + `references/`). Install it
into your agent's skills directory:

```bash
zenstory skill install                     # Claude Code: ~/.claude/skills/zenstory
zenstory skill install --target codex      # ~/.agents/skills/zenstory
zenstory skill install --target openclaw   # ~/.openclaw/skills/zenstory
zenstory skill install --target ./skills   # any skills directory → ./skills/zenstory
zenstory skill path                        # where the bundled copy lives
```

An existing installation is only replaced with `--force`, and only if it is a previous
zenstory skill install (a `SKILL.md` named `zenstory` plus `references/`); any other
directory is left alone. A symlink at the destination is never followed.

Notes on directories: Codex loads user-scope skills from `$HOME/.agents/skills` (older
Codex CLI builds read `~/.codex/skills` — use `--target ~/.codex/skills` if yours does not
pick the skill up). OpenClaw loads managed skills from `~/.openclaw/skills`
(`$OPENCLAW_STATE_DIR/skills` when set) and, in its default state, personal skills from
`~/.agents/skills`. Restart the agent after installing.

## Environment variables

| Variable | Purpose |
|---|---|
| `ZENSTORY_API_KEY` | API key; overrides the saved key |
| `ZENSTORY_API_BASE` | API base URL; default `https://api.zenstory.ai/api/v1` (must include `/api/v1`; https only, except localhost). A saved key is only sent to the base it was saved for — set `ZENSTORY_API_KEY` too, or pass `--allow-base-override`, to use another server |
| `ZENSTORY_LANG` | Default `--lang` (`zh`/`en`) of `projects create`; falls back to `LC_ALL`, then `LANG` (`zh*` → `zh`, else `en`) |
| `XDG_CONFIG_HOME` | Config location: `$XDG_CONFIG_HOME/zenstory/config.json` (default `~/.config/zenstory/config.json`) |
| `XDG_CACHE_HOME` | `files put` backups: `$XDG_CACHE_HOME/zenstory/backups` (default `~/.cache/zenstory/backups`) |

## Exit codes

`0` ok, `1` error, `2` usage (incl. HTTP 400/422), `3` auth/permission (401/403, not logged in), `4` not found,
`5` rate limited (429). With `--json`, errors are printed to stderr as
`{"error": {"message", "status", "code", "exitCode"}}`.

Rate limits are per key, per hour: read 2000, write 1000, search 500, writing-context 500.

## Security

- The key is stored only in the config file, written with mode `0600` inside a `0700`
  directory. The CLI never prints the full key (`eg_1a2b…9f0e`).
- Use the narrowest scopes you need: `read` for browsing and search, `write` only when
  edits are expected. Keys can also be limited to specific projects, and revoked or
  regenerated at any time in Settings → Agent.
- API bases must use https (plain http only for localhost / 127.0.0.1 / ::1).
- Deletes and rollbacks require `--yes`; `files put` refuses to write empty content without
  `--allow-empty`, and `files get -o` never follows symlinks.

## Development

```bash
pnpm --filter zenstory build   # tsc → dist/
pnpm --filter zenstory test    # vitest
node apps/cli/dist/cli.js --help
```

## License

MIT
