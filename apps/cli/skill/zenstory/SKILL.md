---
name: zenstory
description: Reads, writes and organizes the user's novel projects stored in ZenStory (chapters/drafts, outlines, character profiles, lore/world-building, materials) through the ZenStory CLI and its Agent API. Use when the user mentions ZenStory, refers to their novel or story project kept in ZenStory, or asks to read, continue, draft, revise, or reorganize chapters, outlines, characters or settings stored there, or to check continuity across a ZenStory project (e.g. "continue chapter 12 in ZenStory", "add this character to my ZenStory novel", "在 ZenStory 里续写第三章", "把人物设定存到 ZenStory").
compatibility: Requires Node.js 20+ (runs the ZenStory CLI via a global install or npx) and network access to api.zenstory.ai.
metadata:
  version: "0.2.0"
  homepage: "https://zenstory.ai"
  cli: "zenstory"
  api_base: "https://api.zenstory.ai/api/v1"
---

# ZenStory

ZenStory is an AI-assisted novel writing workbench. Everything in a project is a **file**
(outline, draft chapter, character, lore, material, folder) arranged in a folder tree. This
skill operates those files with the `zenstory` CLI, which wraps the public Agent API.

## Running the CLI

Use `zenstory ...` if it is installed (`command -v zenstory`). Otherwise use
`npx -y zenstory ...` with exactly the same arguments. Examples below use `zenstory`.

Always add `--json` when you need to parse output. Errors go to stderr; the exit code tells
you what happened: `0` ok, `2` usage error, `3` auth/permission, `4` not found,
`5` rate limited, `1` anything else.

## Setup check (do this first, once per session)

```bash
zenstory whoami --json
```

- Exit `0`: note `scopes.read` / `scopes.write`. Without `write`, you can only read — tell
  the user before planning edits.
- Exit `3` ("Not logged in" / key rejected): ask the user to create an Agent API key in
  **ZenStory → Settings → Agent** (enable the `write` scope if they want you to edit), then
  run this **themselves** in their own terminal and paste the key when prompted (input is
  hidden, so the key stays out of shell history):

  ```bash
  npx -y zenstory login
  ```

  Self-hosted ZenStory: add `--api-base https://<their-server>/api/v1`.
- Never ask the user to paste the key into the chat, and never put a key on a command line.
  If they already pasted a key into the chat, treat it as exposed: recommend they
  **regenerate it in Settings → Agent** and log in again with the new key as above. Never
  echo, log or repeat a key.

## Orient in a project

```bash
zenstory projects list --json                 # pick the project id
zenstory files tree <projectId>               # folders + titles + ids, no content
zenstory files list <projectId> --type draft --json   # chapters (content omitted)
```

Every project (created in the web app or with `zenstory projects create`) starts with root
folders whose ids are predictable, e.g. `<projectId>-draft-folder` (正文/Drafts),
`<projectId>-outline-folder` (大纲/Outlines), `<projectId>-character-folder`
(角色/Characters), `<projectId>-lore-folder` (设定/World Building),
`<projectId>-material-folder` (素材/Materials); screenplays use `<projectId>-script-folder`.
`projects create` prints them. The user may have renamed, deleted or nested folders, so
confirm with `files tree` before relying on them. See
[references/concepts.md](references/concepts.md) for file types, folders per project type,
and what writing-context returns.

## Workflow: continue or revise a chapter

1. Find the chapter: `zenstory files list <projectId> --type draft --json` (or `search`).
2. Gather context **before writing**:
   ```bash
   zenstory context <projectId> --file <chapterId> --query "what happens next" --json
   ```
   This returns AI-ranked snippets (parent outline, previous chapter, characters, lore,
   retrieved passages). Open any item you need in full with `zenstory files get <source_file_id>`.
3. Note the chapter's current `updated_at`, then read it into a private temp directory
   (use one directory per session and name files by file id):
   ```bash
   zenstory files get <chapterId> --fields id,title,updated_at --json   # note updated_at
   dir=$(mktemp -d)
   zenstory files get <chapterId> -o "$dir/<chapterId>.md"
   ```
4. Edit `$dir/<chapterId>.md` (keep the user's existing text unless asked to rewrite it).
5. Save, passing the `updated_at` you noted so the write is refused if the user changed
   the chapter in the browser meanwhile:
   ```bash
   zenstory files put <chapterId> --content-file "$dir/<chapterId>.md" \
     --if-updated-at <updated_at> --json
   ```
   If it fails with "changed on the server", re-read, re-merge, and retry with the new
   `updated_at`. The server records a version of every content change unless the plan's
   per-file version quota is full (see [Undo](#undo-a-change)); the CLI also saves the
   previous content locally and prints `backupPath` as a second safety net.

`files put` **replaces the whole content**. Never put a partial chapter or a summary back.
It refuses content shorter than half of the current text unless you pass `--allow-shrink`
— only do that after the user confirmed the cut.

## Workflow: write a new chapter

```bash
zenstory context <projectId> --query "chapter 13: the duel at the pass" --json
# write the chapter to "$dir/ch13.md" (dir=$(mktemp -d)), then:
zenstory files create <projectId> --type draft --title "第十三章 关山对决" \
  --parent <projectId>-draft-folder --order 13 --content-file "$dir/ch13.md" --json
```

Match the title pattern of existing chapters (look at `files list --type draft`) and pass
`--order <chapter number>` so chapters sort in reading order (without it the new file goes
after its last sibling; chapter titles like `第N章` / `Chapter N` always sort by N). For a chapter
outline use `--type outline` under the outline folder. Fix the order of an existing file
with `files put <fileId> --order <n>`.

## Workflow: characters, lore and materials

```bash
zenstory search <projectId> "林远" --type character --json      # avoid duplicates first
zenstory files create <projectId> --type character --title "林远" \
  --parent <projectId>-character-folder --content-file "$dir/linyuan.md" \
  --metadata '{"role":"protagonist"}' --json
zenstory files create <projectId> --type lore --title "灵脉体系" \
  --parent <projectId>-lore-folder --content-file "$dir/lingmai.md" --json
```

Materials (素材) use file type `snippet` (`--type material` is accepted as an alias).
`--parent` must be a folder in the same project. To tidy a file that sits in the wrong
place (e.g. at the project root), move it: `zenstory files move <fileId> --parent
<projectId>-character-folder` (`--parent root` moves it to the top level). Create extra
folders (e.g. one per volume) with `files create --type folder --title "第一卷"`.

## Workflow: continuity check

```bash
zenstory search <projectId> "玉佩的来历" --limit 10 --json
zenstory search <projectId> "Chen's scar" --type draft --type outline --json
```

Results carry `id`, `title`, `file_type`, `snippet`, `line_start` and `score`. Open the
source with `files get <id>` before asserting a fact. Newly written files are indexed in
the background, so a just-saved change may take a moment to appear in search.

## Undo a change

Every content change made through the CLI (and the web editor) is recorded as a version,
unless the plan's per-file version quota is full: then the content is still saved, the CLI
prints a warning (`version_quota_exceeded: true` in `--json`) and only the local
`backupPath` copy of the previous content remains. Tell the user when that happens.

```bash
zenstory files versions <fileId> --json            # newest first, no content
zenstory files version <fileId> <n> -o "$dir/<fileId>-v<n>.md"   # inspect version n
zenstory files rollback <fileId> <n> --yes --json  # restore it; prints new_version_number
```

A rollback keeps the history: the restored content becomes a new version, so it can be
undone too. Ask the user before rolling back. If `new_version_number` is `null`, the
content was restored but no new version was recorded — because the per-file version quota
is full when `version_quota_exceeded` is `true`, otherwise because the snapshot could not
be saved. Files created with content
through the CLI start at version 1; the local `backupPath` copies from `files put` remain a
second net (e.g. for files whose history started before the CLI touched them).

## Safety rules

- **Confirm before destructive actions.** Ask the user before `files delete` /
  `projects delete` / `files rollback` (all need `--yes`) and before `files put` that would
  shrink or replace substantial existing text (e.g. more than a few paragraphs). Show what
  will change.
- **Read before you write.** Never overwrite a file you have not just read; always pass
  `--if-updated-at` to `files put`.
- **Respect rate limits** (per key, per hour): read 2000, write 1000, search 500,
  writing-context 500. Use `files list --fields` / `files tree` instead of fetching every
  file's content, don't poll, and stop on exit code `5` and tell the user.
- **Keep secrets out of output.** The CLI masks the key; never print config files or
  environment variables that contain it.
- **Stay in scope.** Only touch the project the user named. If a command fails with exit
  `3` on write, the key likely lacks the `write` scope or is limited to other projects —
  report it instead of retrying.
- Prefer `--json` for anything you parse; use human output only to show the user.

## Reference

- [references/cli.md](references/cli.md) — every command, flag, endpoint and JSON shape.
- [references/concepts.md](references/concepts.md) — file types, folder conventions,
  writing-context contents, ordering caveats.
