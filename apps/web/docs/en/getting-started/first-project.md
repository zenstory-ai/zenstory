# Write your first short story in ZenStory: idea, files and revision

In the ZenStory browser workbench, separate story facts from open ideas, save characters and scene plans as project files, draft one scene at a time, quote passages for local revision, and export the prose. This tutorial follows a lost-property story rather than asking AI to finish a whole book at once.

Open the [hosted workbench](https://app.zenstory.ai/dashboard); installing Oh Story or a coding agent is not required. For the shorter button-by-button path, see [Quick Start](https://zenstory.ai/docs/getting-started/quick-start). The characters, filenames and requests below are original teaching examples, not a recorded model run.

## 1. Create an empty short-story project

1. Sign in, open Dashboard and choose **Short Story** from the available types.
2. **Leave the idea box empty for this exercise** and click **Start Writing**. This creates the workspace before you send a narrowly scoped request. Quick creation uses a default name; use **Edit project name** in the project switcher to rename it **Before Closing**.
3. The project opens with files, an editor and AI chat. On a narrow screen, switch to the area you need.

An idea supplied during creation may be sent automatically as a brainstorming request after the project opens. That is not a promise to produce an outline and Chapter 1; avoid submitting the same task again while it is running. Empty-idea creation does not trigger that idea auto-send path.

The default English templates are below. They organize files, not guaranteed output lengths or quality; existing projects may have customized folders.

| Type | Default folders | How to use it |
| --- | --- | --- |
| Long-form Novel | World Building, Characters, Materials, Outlines, Drafts | Work chapter by chapter from the outline |
| Short Story | Characters, Concept, Materials, Drafts | This exercise plans three scenes |
| Screenplay | Characters, World Building, Materials, Episode Outlines, Scripts | Organize episodes and scenes; this does not generate a video |

## 2. Give story boundaries, not guesses disguised as facts

Start with a brief fact card. Substitute your own premise, but retain the distinction between what you have decided and what remains open.

| Established | Still open |
| --- | --- |
| The station's lost-property desk closes at six; it is now 5:40 | Why Shen Min describes the wrong object |
| Lin He staffs the desk; Shen Min claims a gray canvas bag | How she is related to its owner |
| She describes the exterior correctly but gets an inner-pocket item wrong | Who eventually receives the bag |
| Lin He cannot release it on a verbal claim alone | Endings may be proposed, not treated as established history |

Send this in chat:

```text
I am writing a realist short story, Before Closing, in Lin He's limited viewpoint.
The station's lost-property desk closes at six; it is now 5:40. Shen Min claims
a gray canvas bag. She describes the outside correctly but gets an inner-pocket
item wrong. Lin He cannot release it on a verbal claim alone.

Reply in chat with a three-scene outline only: goal, obstacle and change for each.
Separate established facts from proposed additions. The reason for the mistaken
item and the bag's eventual recipient are undecided. Do not assume theft or add
a supernatural explanation. Do not draft prose or modify files yet.
```

“Do not modify files” is an instruction to the agent, not a product-enforced read-only switch. The agent can operate on project files: watch file changes and operation results as well as the reply. For more direct editorial control, request suggestions and apply them manually.

## 3. Choose the outline, then keep files you can write from

Does each scene change the situation? Does Lin He know more than the available evidence permits? Does the ending address the question you want to explore? Tell the AI which cause and ending you chose rather than leaving several alternatives to become contradictory “facts.”

After deciding, request **only these files**, or create them through the file tree and paste your approved material:

| Folder / example title | File type | Contents |
| --- | --- | --- |
| Characters / Lin He | `character` | Goal, responsibilities and knowledge boundaries |
| Characters / Shen Min | `character` | Confirmed background; unresolved motives marked as open |
| Concept / Three-scene outline | `outline` | Chosen plot, scene changes and when readers learn key facts |
| Drafts / Scene 1 — The claim | `draft` | Narrative prose, not analysis or revision notes |

These are files you request, not automatically generated names. **Type matters**: naming a file “Draft” does not make it an exportable draft. Materials can hold reference notes you have the right to use; this exercise does not need a whole reference novel uploaded.

## 4. Open the target and choose this request's context

Open **Scene 1 — The claim**. The request identifies the focused file, and the server reads its saved content under a context budget; this does not include the whole book every time. Attach supported material items, quote key character or outline passages, or name files to read. Not every file type has the material attachment control.

Save recent character and outline edits, quote essential constraints and request a read of the relevant files. Then ask:

```text
First read the approved Three-scene outline and character files, then draft only
Scene 1 — The claim, around 400–600 English words, in Lin He's limited viewpoint.
Stop when Shen Min names the wrong inner-pocket item. Convey urgency through
actions and questions; do not reveal the reason or explain another person's mind.
Write into the open draft file, not a duplicate. Do not continue to Scene 2.
If an essential fact is missing, ask; do not turn guesses into character facts.
```

Length is an editorial target, not an exact-output guarantee. Text appearing in a reply does not prove it was saved in the intended file: open that file. If the response is only a suggestion, apply it manually or explicitly request the file update.

## 5. Quote the problem instead of rewriting everything

Suppose the draft says “Lin He knew Shen Min must be lying,” but you want to preserve ambiguity:

1. Select that sentence and the necessary surrounding text. Add it to chat using the selection toolbar's quote control.
2. Explain which established fact it violates and the permitted edit scope.
3. If a diff review appears, accept or reject changes before finishing it. Other agent file operations do not necessarily use the same per-change confirmation interface.

```text
Revise only the quoted passage. Lin He knows the description does not match,
but not why. Replace the accusation of lying with an observable action or a
follow-up question. Keep the outcome: the bag has not been handed over.
Suggest replacement text in chat first. Do not edit files, change motives,
or touch other scenes.
```

Apply the suggestion to the selected passage when satisfied and read the transition around it. If you instead decide to change a motive or ending, update the character file and outline separately; a prose edit is not an automatic synchronization of every story fact.

## 6. Save, retain important originals and export the prose

Ordinary editing supports autosave and **Save**; check the save status before leaving. When AI is editing the same file, let that operation finish before competing edits. If there is a conflict, compare both versions instead of refreshing away unsaved local work.

History snapshots support comparison and rollback, but snapshot rules and version allowances apply. They are not a permanent backup of every keystroke. A warning that prose was saved without a new version distinguishes content saving from history creation. Keep separate copies of important originals; do not delete files to experiment with recovery.

Use **Export** in the project header, or its mobile menu, to download TXT. Current export combines non-deleted `draft` and `script` files with their titles and text. Chapter/episode numbering and file ordering affect the sequence; it is **not simply creation order**. Check the downloaded scene order, ending and missing passages.

This does not export all characters, materials, chat or version history and is not a complete project backup. Missing writing files, permissions or plan-format restrictions can prevent export; follow the actual error. This tutorial does not promise DOCX, EPUB or automatic publication.

For the focused-file, material-attachment and text-quotation distinctions, see the [AI assistant guide](https://zenstory.ai/docs/user-guide/ai-assistant).

## Continue with a deliberate next scene

Once you have a usable scene, reuse the approved files and specify the next scene's change and stopping point. You can also write manually and use AI only when stuck or revising.

- Choosing a browser workspace or local skills? See the [writing-workflow comparison](https://zenstory.ai/compare/writing-workflows).
- Want a before/after editing example? See [revising formulaic prose](https://zenstory.ai/oh-story/revise-ai-prose). Its editorial principles can help, but Oh Story's file workflow is not a set of workbench buttons.
- For account entrypoints, see [registration and login](https://zenstory.ai/docs/getting-started/installation). Account rules, quotas and model-service availability depend on the actual deployment.

## Implementation sources

Source checked on 2026-09-12. These fixed-version references explain the interface; they are not a live-account, model-output or current-plan test.

- [Empty-idea quick creation](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/pages/DashboardHome.tsx#L511-L547)
- [Type selection and creation controls](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/pages/DashboardHome.tsx#L653-L720)
- [Chinese and English project templates](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/config/project_templates.py#L15-L135)
- [Focused file, attachments and quotes](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/ChatPanel.tsx#L1151-L1177)
- [Initial-idea send conditions](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/ChatPanel.tsx#L1238-L1287)
- [Quoting a selection](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SimpleEditor.tsx#L348-L354)
- [Autosave and snapshot intent](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SimpleEditor.tsx#L509-L554)
- [Conflict and successful-save handling](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Editor.tsx#L327-L394)
- [Saving a diff review](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Editor.tsx#L397-L446)
- [User-version allowance](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/file_version_service.py#L536-L555)
- [Project export control](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Header.tsx#L228-L241)
- [Exported file types and ordering](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/export_service.py#L110-L143)
- [TXT composition](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/export_service.py#L197-L203)
- [Export restrictions and download response](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/api/export.py#L87-L143)
- [Server reads of focused files and attached references](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/agent/context/assembler.py#L145-L177)
- [Project-material attachment control](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/sidebar/FileTreePane.tsx#L552-L575)
