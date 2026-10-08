# ZenStory Workbench Quick Start: From an Empty Project to a Reviewable Outline

This page walks you through the first round: sign in to the workbench, create a project, get to know the file and chat areas, then ask the AI for an outline you can edit.

Open the app at [app.zenstory.ai](https://app.zenstory.ai).

## 1. Sign in and create a project

1. Open [Login](https://app.zenstory.ai/login), then go to the [Dashboard](https://app.zenstory.ai/dashboard). No account yet? See [Account Registration and Login](https://zenstory.ai/docs/getting-started/installation).
2. Choose Novel, Short Story or Short Drama Script; the type sets up the starting file structure, which you can keep reorganizing later.
3. The idea box can stay empty: press Enter or click **Start Writing** and the project is created. Leaving it empty is a good choice if you want to look around before sending your own first request.
4. If you did type an idea, open the project and look at what actually appeared in the chat and files.

## 2. Learn the three working areas

- **File area:** browse the project tree and click a file to open it.
- **Editor:** read or change the current file and watch its save state.
- **Chat:** give the AI a task and see its reply and tool results.

The AI can query, create, edit and delete project files, and it doesn't always ask first. For planning only, say "do not create or modify files"; that is an instruction to the AI, not a read-only switch. When you are ready to save work, name the target file, what may change and where to stop.

## 3. Start with one narrow request

Here is an original example:

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

Before saving the outline, create or open its target outline file and ask: "Write the approved three-scene outline into the current file." The request carries that file's saved content; the AI reads a limited amount of context each round and does not pull in the whole project.

Attach material items to the chat when they help. For character cards and earlier chapters, quote the key text or name the files to read. For a local edit, quote the passage and name its file and section. The more precise the context, the less likely the AI edits the wrong file or widens the change.

## 5. Saving, version history and export

The editor saves automatically; watch for saved or conflict messages. Version history lets you view and restore earlier versions; once a file reaches your plan's version limit, your text still saves, just without new versions, so keep your own copy of important milestones.

The download button in the project header combines your manuscript and script files into one TXT in chapter order. It is not a backup of the whole project: outlines, characters, settings, chat and version history are not included.

For how focused files, material attachments and text quotations differ, see the [AI assistant guide](https://zenstory.ai/docs/user-guide/ai-assistant).

## Next steps

- Follow [Write your first short story in ZenStory: idea, files and revision](https://zenstory.ai/docs/getting-started/first-project) for a complete example.
- Read the [ZenStory Workbench overview](https://zenstory.ai/workbench) to see how it differs from using a skill pack in an agent.
- Use the [writing environment comparison](https://zenstory.ai/compare/writing-workflows) to choose between ZenStory Workbench, Oh Story and Oh Story DSH.

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
