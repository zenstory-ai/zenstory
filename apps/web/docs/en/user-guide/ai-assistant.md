# AI writing assistant: context, scoped tasks and continuation

In ZenStory, AI can read and operate on project files as well as return text. Before a writing task, identify the target, the story facts it depends on, the permitted changes and where to stop. Keep approved decisions in files and focused quotations rather than relying only on a long conversation.

This guide is for the [ZenStory browser workbench](https://app.zenstory.ai/dashboard), not Oh Story commands. Start with [Quick Start](https://zenstory.ai/docs/getting-started/quick-start) if needed. Requests below continue the lost-property example from [your first short story](https://zenstory.ai/docs/getting-started/first-project); they are original teaching examples, not recorded AI results.

## 1. Choose this task instead of requesting the whole novel

Use the project's AI chat area. Distinguish discussion, drafting and file changes:

| What you need | This task's deliverable | Boundary to specify |
| --- | --- | --- |
| An unresolved plot decision | Two alternatives with consequences | Discuss in chat; do not make unchosen ideas established facts |
| A scene draft | Prose for a named scene | Target, viewpoint, approximate length and stopping point |
| A character-consistency check | Original sentences compared with settings | Find evidence before suggesting edits; do not rewrite the chapter |
| An approved revision | A specific passage replacement | Facts to preserve and whether other files may change |

A useful request has five parts: **goal, references, established facts, permitted changes and stopping point**. It need not be long. Do not label conflicting alternatives as simultaneous requirements.

## 2. What content can the AI see?

The browser identifies the focused file, and the server reads its saved content within the project and assembles related material. It is not limited to seeing the filename. Nor does every request necessarily contain the entire book, every reference and all conversation history.

File context and chat history have budgets; material can be selected or compacted. Save important changes before requesting work, and briefly restate critical constraints instead of relying on an early chat message alone.

| Input | How to provide it | Useful contents |
| --- | --- | --- |
| Focused file | Open the file for this task | The saved scene, character or outline |
| Project material | Use the material item's add-to-chat control | Reference notes; this control is not a universal attachment button for every file type |
| Library material | Use a supported item's attach-to-chat control | Relevant reference information you have the right to use |
| Text quotation | Open a file, select text and add it to chat | The sentence to revise or a rule that must remain true |
| Other project files | Name them and request a read; quote crucial passages if needed | Approved character and outline references, without copying every chapter |

The current web interface shares **five attachment slots** between project materials and library items. Text quotations have a separate **five-quote limit**. Remove irrelevant items when full; a slot does not guarantee the item's full text survives the final context budget.

Characters, outlines and drafts do not necessarily have the material item's attachment control. Open and quote the relevant passage, or name the file to read. Do not paste an entire character sheet into an unrelated file just to simulate an attachment. If you edit the original after quoting it, check the quotation before sending.

## 3. How can a continuation preserve an unresolved truth?

Suppose Scene 1 ends with Shen Min naming the wrong item inside the bag. The bag has not been released. Lin He knows the description is wrong, but not why. Save relevant files, open Scene 2's target draft and ask:

```text
Continue Scene 2 of Before Closing. First read Three-scene outline, Lin He,
Shen Min and the Scene 1 draft. If names are ambiguous, list the candidates
so I can identify the target; do not guess.

Established facts: the description is wrong; the bag remains at the desk;
Lin He does not know why Shen Min made the mistake.
Write only Lin He's request for further identification and Shen Min's response.
Keep Lin He's limited viewpoint. Use actions and questions; do not turn suspicion
into a proven lie. Give a candidate passage of around 400 English words in chat,
without editing files. Stop at the next decision. Ask about missing essential
facts instead of deciding the truth for me.
```

This states what the task may decide; it is not a guarantee against model mistakes. First check whether anyone knows something they have not learned, then judge the prose. Length is an editorial target.

When satisfied, specify where the chosen passage belongs and whether to replace or append. Do not let “continue” mean both “draft more” and “save everything correctly.”

## 4. How do I find and repair a character contradiction?

Instead of “make the character more consistent,” supply an observable mismatch and its reference:

```text
Compare the current Scene 2 with the character setting I quoted.
Reply only with: problem sentence, conflicting setting and smallest suggested edit.
Established fact: Lin He knows the description does not match, but not why. Add no background and modify no files.
Mark unsupported decisions as “for the author to decide.”
```

“Suggest, do not edit” is a task instruction, not enforced read-only mode. Available agent tools include creating, editing and deleting files; do not assume every write waits for a confirmation dialog. Save important originals, inspect actual files and operation results, and decide individual changes if a diff review appears.

See [the editor guide](https://zenstory.ai/docs/user-guide/editor) for selection, a before/after example and review controls. If an adopted edit genuinely changes a motive or outcome, update the related character and outline separately; prose edits do not automatically synchronize every story fact.

## 5. How do I continue without repeating a long conversation?

Keep approved decisions in their project files and use a short handoff for the next task. This is a chat format, not an automatically executed workflow file:

```text
Task: continue Scene 2, not the ending.
Completed: Scene 1 is saved; the bag has not been released.
References: current Three-scene outline, Lin He, Shen Min and Scene 1.
Must remain true: Lin He does not know the reason for the wrong description.
Open choices: Shen Min's next evidence and the bag's eventual recipient.
First action: read those files and identify one choice needing my decision;
do not draft yet.
```

The plus-shaped **New Session** control at the top of chat starts a new active session and clears the current message display. It **does not delete project files** or erase all project state. Record decisions and unfinished work first; check the focused file, attachments and quotations afterward.

The old conversation is no longer the active session. Do not treat a new session as undo, or make your writing plan depend on complete retrieval and restoration of old chat. Incorrect facts already saved in project files still need correction.

## 6. What if generation stops or the response is interrupted?

**Stopping generation is not undo.** An interruption can leave partial replies, operation records and file changes. Not seeing the final completion message does not mean nothing happened.

1. Preserve important unsaved editor text before refreshing or clearing anything.
2. Inspect the reply, operation results and target file: distinguish suggestions, partial saved work and unfinished work.
3. Scope recovery: “Read the current Scene 2 and list completed and missing portions; do not edit yet.” Then continue only the missing passage instead of appending the whole scene again.
4. Follow quota or access errors if shown. Repeated “continue” requests do not resolve those limits.

Status messages and tool results help locate progress. They do not expose all of a model's internal reasoning or establish publication quality.

## Next steps

- [Editor guide](https://zenstory.ai/docs/user-guide/editor): manual writing, quotations, local revision and save state.
- [First short-story walkthrough](https://zenstory.ai/docs/getting-started/first-project): connect outline, characters and prose.
- [Choose a writing workflow](https://zenstory.ai/compare/writing-workflows): distinguish the browser workbench, Oh Story and the DSH plugin.

## Implementation sources

Source checked on 2026-09-12. These fixed-version references explain the workflow; they do not establish live-account results, model output or complete history recovery.

- [Request fields for focused files, attachments and quotations](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/ChatPanel.tsx#L1151-L1177)
- [Server reads of focused files and attached references](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/agent/context/assembler.py#L145-L177)
- [Context selection under a budget](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/agent/context/assembler.py#L239-L266)
- [Project-material attachment control](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/sidebar/FileTreePane.tsx#L552-L575)
- [Library-material attachment control](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/sidebar/MaterialsPane.tsx#L342-L351)
- [Shared attachment slots and types](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/contexts/MaterialAttachmentContext.tsx#L18-L96)
- [Quotation slots and payload](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/contexts/TextQuoteContext.tsx#L5-L56)
- [Agent file-operation tools](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/agent/tools/registry.py#L17-L42)
- [Budgeted conversation-history window](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/agent/core/session_loader.py#L250-L273)
- [New-session interface behavior](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/ChatPanel.tsx#L1312-L1340)
- [Creating an active session rather than deleting a project](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/api/chat.py#L115-L152)
- [Partial-history handling after cancellation or disconnection](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/agent/service.py#L1096-L1113)
