# ZenStory Workbench Quick Start: From an Empty Project to a Reviewable Outline

This quick start covers one focused writing session: create a project, inspect the workspace, and request a reviewable outline. Project creation does not guarantee an automatic first chapter, and version history is not unlimited backup.

Open the hosted workbench at [app.zenstory.ai](https://app.zenstory.ai).

> This source-reviewed guide did not create an account or call AI. Registration rules depend on the current deployment; see [Account Registration and Login](https://zenstory.ai/docs/getting-started/installation).

## 1. Sign in and create a project

1. Open [Login](https://app.zenstory.ai/login), then go to the [Dashboard](https://app.zenstory.ai/dashboard).
2. Choose a project type; it establishes the initial file structure.
3. The inspiration box may be empty. Press Enter without Shift or choose **Start Writing**. Leave it empty if you want to inspect the workspace before sending your own request.
4. If you enter an idea, inspect the resulting chat and files rather than assuming it produced an outline or prose.

## 2. Learn the three working areas

- **File area:** browse the project tree and click a file to open it.
- **Editor:** read or change the current file and watch its save state.
- **Chat:** give the AI a task and inspect its reply and tool results.

The AI can query, create, edit and delete project files. Do not assume every write waits for confirmation. For planning only, say “do not create or modify files”; this is not an enforced read-only mode. When saving work, name the target, permitted change and stopping point.

## 3. Start with one narrow request

This is an original example, not recorded model output:

```text
Plan a realistic mystery short story as a three-scene outline only.
Do not write prose or create or modify files.

It is 5:40 p.m.; the station lost-property desk closes at six. Lin He handles Shen Min's
claim for a gray canvas bag. She describes its exterior correctly but names the wrong
object in its inner pocket. Do not conclude that she stole it or decide the truth.
Lin He cannot release the bag on a verbal claim alone.

For each scene, give the location, immediate goal, visible action, information change
and closing question. End with two choices for me to decide, then stop.
```

Check whether the scenes escalate before deciding why Shen Min was wrong, whether Lin He is withholding something, or whether to draft prose.

## 4. Open the target and add relevant context

Before saving the outline, create or open its target outline file and ask: “Write the approved three-scene outline into the current file.” The request identifies the focused file, and the server reads its saved content under a context budget; this does not automatically include the whole project.

Attach supported material items. For character cards and earlier chapters, quote key text or name the files to read; not every file has a material attachment control. For a local edit, quote the passage and name its file and section. Precise context reduces wrong-file edits and scope expansion.

## 5. Understand save, history and export boundaries

Watch the editor for saved or conflict messages. History can inspect or restore retained versions, but snapshot rules and version allowances apply. Content may still save without a new snapshot after the version quota is reached. Keep milestone backups elsewhere.

The project header downloads a merged TXT from non-deleted `draft` and `script` files. Chapter/episode numbering and file ordering affect the sequence, with creation time used as a fallback. This is **not a project backup**: outlines, characters, lore, chat and full history are excluded.

For the focused-file, material-attachment and text-quotation distinctions, see the [AI assistant guide](https://zenstory.ai/docs/user-guide/ai-assistant).

## Next steps

- Follow [Write your first short story in ZenStory: idea, files and revision](https://zenstory.ai/docs/getting-started/first-project) for a complete example.
- Read the [hosted workbench overview](https://zenstory.ai/workbench).
- Use the [writing workflow comparison](https://zenstory.ai/compare/writing-workflows) to choose Hosted ZenStory, Oh Story or Oh Story DSH.

## Reviewed source

Source checked on 2026-09-12.

- [Blank-inspiration quick creation and navigation](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/pages/DashboardHome.tsx#L511-L547)
- [How focused files, attachments, materials and quotes enter a request](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/ChatPanel.tsx#L1151-L1177)
- [Project-file tools available to the AI](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/agent/tools/registry.py#L17-L42)
- [Saving content while skipping a snapshot at the version limit](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/api/files.py#L899-L927)
- [Desktop export and version-history entries](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Header.tsx#L228-L249)
- [TXT file types, ordering and merged content](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/export_service.py#L110-L203)
- [Server reads of focused files and attached references](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/agent/context/assembler.py#L145-L177)
- [Project-material attachment control](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/sidebar/FileTreePane.tsx#L552-L575)
