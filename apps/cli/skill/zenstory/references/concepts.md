# ZenStory concepts for agents

Derived from the ZenStory backend (`models/file_model.py`, `config/project_templates.py`,
`api/agent_api.py`, `agent/context/assembler.py`).

## Contents

- [Projects](#projects)
- [Files and file types](#files-and-file-types)
- [Folder conventions](#folder-conventions)
- [Ordering](#ordering)
- [Version history](#version-history)
- [Writing context](#writing-context)
- [Search](#search)
- [API keys and scopes](#api-keys-and-scopes)

## Projects

A project contains one work. Its `project_type` is one of:

| Type | Meaning |
|---|---|
| `novel` | Long-form serialized novel (the default) |
| `short` | Stand-alone short story; the project type imposes no word-count limit |
| `screenplay` | Short-drama / screenplay project; body text uses `script` files |

## Files and file types

Everything inside a project is a **file** with `title`, `content` (plain text/Markdown),
`file_type`, optional `parent_id` (folder), `order` and `file_metadata` (a JSON string).

| `file_type` | Used for | Suggested metadata keys |
|---|---|---|
| `draft` | Chapter / body text (正文) | `chapter_number`, `word_count`, `status` |
| `outline` | Story, volume or chapter outlines (大纲) | `chapter_number`, `status`, `word_count_target` |
| `character` | Character profiles (角色/人物) | `age`, `gender`, `role`, `personality`, `appearance` |
| `lore` | World-building: places, factions, power systems (设定) | `category`, `importance`, `tags` |
| `snippet` | Materials / reference notes (素材). The UI calls these "materials". | `source`, `tags`, `importance` |
| `script` | Screenplay episodes (剧本, screenplay projects) | `episode_number`, `scene_count`, `duration` |
| `document` | Generic document (default type in the web app) | `tags`, `word_count` |
| `folder` | Organizes other files; content is empty | – |

Metadata is optional and free-form; the keys above are the conventions the web app and
the built-in AI understand. `--metadata` can only be set when creating a file.

## Folder conventions

Every project — created in the **web app** or through the **Agent API**
(`zenstory projects create`) — gets root folders with predictable ids
`<projectId>-<key>`:

| Project type | Folders (order) → file type stored inside |
|---|---|
| `novel` | `lore-folder` 设定/World Building → lore; `character-folder` 角色/Characters → character; `material-folder` 素材/Materials → snippet; `outline-folder` 大纲/Outlines → outline; `draft-folder` 正文/Drafts → draft |
| `short` | `character-folder` 人物/Characters → character; `outline-folder` 构思/Concept → outline; `material-folder` 素材/Materials → snippet; `draft-folder` 正文/Drafts → draft |
| `screenplay` | `character-folder` 角色/Characters → character; `lore-folder` 设定/World Building → lore; `material-folder` 素材/Materials → snippet; `outline-folder` 分集大纲/Episode Outlines → outline; `script-folder` 剧本/Scripts → script |

Folder titles are Chinese or English depending on the user's language at creation time
(Agent API: the `Accept-Language` header, Chinese by default). Users may rename or delete
folders and add subfolders (e.g. per volume: `第一卷`) — check `zenstory files tree`.

Working with folders:

- Put each file under the folder for its type. The API only accepts a folder of the same
  project as parent; move misplaced files with `files move <fileId> --parent <folderId>`.
- Match the naming pattern already used (e.g. `第十二章 标题` vs `Chapter 12: Title`).
- Before creating a character/lore entry, search for an existing one to update instead.

## Ordering

`order` controls position among siblings (lower first); the API lists by `order`, then
newest first. Set it with `files create --order <n>`, `files put <fileId> --order <n>` or
`files move ... --order <n>`. Without `--order`, a new file takes the chapter number found
in its title or metadata (`chapter_number`, `episode_number`, ...), else it goes after its
last sibling. For `draft`, `outline` and `script` files a chapter-like title (`第N章`,
`第N集`, `Chapter N`) always sorts by N — the title wins over an explicit `--order`,
exactly as in the web app. The writing-context
assembler uses `order` to find the previous chapter; use chapter numbers for this field.

## Version history

Each file has a version history shared with the web app's history panel. The Agent
API records non-empty creation and content changes; title/order-only updates and moves
record none. Web-editor saves may coalesce into one recent user version, while the
built-in writing agent records AI versions under its own path.
Versions are numbered from 1 per file; `files versions` lists them newest first.
`files rollback <fileId> <n> --yes` restores version n, recording a new restore version
when the user quota permits. Before a restore, current text not already in history is
saved as a system backup version; a backup failure aborts the restore.

The Agent API saves content even when the user version quota is full, returning
`version_quota_exceeded: true`. The CLI warns. For `files put`, retain the local
pre-write backup in `~/.cache/zenstory/backups`; the new text may have no server version.

## Writing context

`zenstory context <projectId> [--file <id>] [--query text]` calls the same context
assembler the built-in ZenStory AI uses. It can include:

- The focus file (`--file`), its parent outline, the previous chapter and a few
  recently edited sibling chapters.
- Up to 10 character and 10 lore files from the project.
- Retrieved passages from keyword search when `--query` is given (`snippet` items).

Items are deduplicated, ranked by priority and query relevance, and trimmed to a token
budget. Each item has `type` (`outline`, `character`, `lore`, `snippet`, `quote`, ...),
`title`, `content_snippet` (up to 500 original characters plus `...` when truncated), `source_file_id` and `relevance`. Note: body
text files (`draft`, `script`, `document`) are currently reported with `type: "outline"`
— check the real type with `files get <source_file_id> --fields file_type --json` when it
matters. Snippets are
previews: fetch full text with `zenstory files get <source_file_id>` when details matter.
`--max-items` is 1–30 (default 10); the server caps the summed serialized entry
lengths near 50 K characters.

## Search

`zenstory search` reads the project's current database files using keyword matching.
Each hit has `score`, `fused_score` (the same keyword score), `sources` (`lexical`),
a `snippet` and `line_start`. Committed edits and deletions are visible immediately;
there is no background index or embedding provider. HTTP 503 means database search
is busy or unavailable; fall back to `files list` + `files get`.

## API keys and scopes

Keys look like `eg_` + 64 hex characters and are created in ZenStory
**Settings → Agent**. Scopes: `read` (list/get/search/context) and `write`
(create/update/move/rollback/delete). New keys default to `read` only. A key can optionally be limited
to specific projects; other projects then return 403 (or are hidden from
`projects list`). Deletes are soft deletes, but deleted files are not reachable through
the Agent API afterwards — treat them as permanent from the agent's point of view.
