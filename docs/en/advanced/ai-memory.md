# AI Memory & Context: separate settings, progress and open ideas

> Repository snapshot (2026-09-13). Use the [current hosted guide](https://zenstory.ai/docs/advanced/ai-memory) for the web workbench. This page reuses [the served guide source at this revision](https://github.com/zenstory-ai/zenstory/blob/1773af4781996363496f47268cce0e7c640108c8/apps/web/docs/en/advanced/ai-memory.md), retaining the explanation and worked example for reading in the repository. The date identifies this page’s source version, not the freshness of other repository documents.

ZenStory's **AI Memory is an editable project summary**, not proof that the AI has read your whole book or a guarantee that characters never drift. It stores four fields—project summary, writing style, current phase and notes—for context assembly. Keep detailed characters, outlines and prose in their respective files, and identify the evidence needed for each task.

This guide covers the [hosted ZenStory workbench](https://app.zenstory.ai/dashboard), not Oh Story tracking files or the DSH plugin. New users can start with the [browser quick start](https://zenstory.ai/docs/getting-started/quick-start).

## 1. What belongs in memory, and what stays in files?

| Content | Useful location | Do not confuse it with |
| --- | --- | --- |
| Stable genre, viewpoint and important constraints | A short memory summary, with detailed evidence in setting files | A promise that the AI will remember a statement forever |
| Character backgrounds, ability limits and full outlines | The relevant character, setting and outline files | All files automatically collected into the four memory fields |
| Events already written | Prose files, with a short progress note in Current Phase | Events planned for the next scene already completed |
| Unchosen motives, identities or endings | Planning files or notes explicitly marked undecided | Established facts or knowledge a character already has |
| A change requested only for this turn | The current message, quotation and target file | A permanent change to the whole project's style |
| Ideas discussed in chat | Conversation history; move adopted decisions into files or the summary | Every suggestion automatically becoming permanent memory |

Starting a new conversation and changing project memory are different operations. Do not try to clear an incorrect saved setting by switching conversations, or make complete recovery of old chat a prerequisite for writing. See the [AI assistant guide](https://zenstory.ai/docs/user-guide/ai-assistant) for focused files, attachments, quotations and session handoffs.

## 2. Open, edit and save

1. In the project's chat header, choose the database icon with the AI Memory tooltip.
2. Read the four fields and edit only what needs updating.
3. Choose Save. A successful save closes the dialog; if saving fails, preserve your input and address the message. Closing a panel alone is not evidence of a save.
4. After an important correction, reopen the panel to check the stored text. Cancel does not submit your edits; do not rely on an additional unsaved-exit warning always appearing.

Save is the relevant action here. If other actions such as sharing an inspiration are visible, they are not required to save memory.

| Field | Suggested content | Current source-defined storage limit |
| --- | --- | --- |
| Project Summary `summary` | Genre, central situation, viewpoint and a few accepted constraints | 4,000 characters |
| Writing Style `writing_style` | Expression choices to preserve and approaches to avoid | 1,000 characters |
| Current Phase `current_phase` | What is saved, the current task and what remains unfinished | 1,000 characters |
| Notes `notes` | Easily confused facts, relevant file names and clearly marked open questions | 4,000 characters |

These are storage limits, not recommended lengths, token counts or a promise that every character reaches the model. Context has a budget; long Summary and Notes text can be shortened. Put the important constraints first, keep long character records and chapter history in files, and quote the key passage again when it matters.

Manual edits need saving, but **the Agent also has a tool that updates project memory**. Not pressing this panel's Save button does not prove that no Agent update happened. Do not assume every tool update first opens a per-change approval dialog. Asking for suggestions only is a task boundary, not an enforced read-only permission.

## 3. Original example: scene one is written, not the ending

This exercise continues the lost-property story from the [first-story tutorial](https://zenstory.ai/docs/getting-started/first-project). It assumes the author has saved scene one; it is **not a real account, model output or automatically generated memory**. The file titles match the English first-story tutorial; substitute the actual titles in your project.

Established conditions: Lin He runs the station's lost-property window, and Shen Min claims a gray canvas bag. Shen describes its appearance correctly but gets an inner-pocket item wrong. Scene one ends at that discrepancy. The bag has not been handed over; Lin does not know why the description is wrong. The author has not chosen that reason, Shen's relationship to the owner or the eventual recipient. The story uses Lin's limited viewpoint, and a spoken claim alone does not authorize releasing the bag.

**An unsuitable progress summary:**

> Lin exposed Shen as a fraud and has returned the bag to its true owner. Next, write Shen's regret.

This upgrades an undecided identity to fact, records an unperformed handover as complete and invents another person's inner state. Repeatedly supplying that wrong summary does not make continuation more reliable.

**A more useful four-field draft:**

```text
Project Summary: Realist short story Before Closing, in Lin He's limited viewpoint.
The station's lost-property window closes at six; the opening is at five forty.
Lin cannot release the gray canvas bag on a spoken claim alone.

Writing Style: Move the scene through questions, answers and observable action.
Necessary interiority is allowed, but stay within what Lin currently knows;
do not explain other people's private thoughts.

Current Phase: Scene one is saved up to the incorrect inner-pocket description.
The bag is not handed over. This turn only discusses further checks in scene two,
not the ending; scene two remains unwritten.

Notes: [Author undecided] Reason for the error, Shen's relationship to the owner,
and the bag's final recipient.
[Continuation boundary] A mismatched description is not proven lying.
[Evidence files] Lin He, Shen Min, Three-scene outline and Scene 1 — The claim.
```

The bracketed labels are the author's plain-text convention, not special system fields. Naming a file here is not an instruction that automatically reads it. When writing scene two, explicitly request those files and quote the important material if needed.

The summary provides a useful entry point without trying to hold the entire story. If the author later chooses a motive, record it as an author decision and separately decide when a character learns it. Author knowledge is still not Lin's knowledge.

## 4. Correct a wrong memory without rewriting the story

Find the evidence before deciding which layer to change. A wrong summary does not prove the prose is wrong. Changed prose does not mean every character file, outline and summary has already been synchronized.

Start with a request like this:

```text
Read Lin He, Shen Min, Three-scene outline and Scene 1 — The claim first.
If several files share a title, list the candidates instead of guessing.
Compare them with current AI Memory. List only each conflicting sentence,
its file evidence and a proposed replacement.

Confirmed: scene one ends at the mismatched description; the bag is not released,
and Lin does not know the reason.
Provide a four-field correction draft in chat only. Do not update memory or files.
Keep undecided identities and motives open; do not choose the ending or write scene two.
```

After reading the proposal, you can edit and save the fields yourself. If you explicitly ask the Agent to write the update, name the fields and accepted text. Inspect actual memory and relevant files afterward; “I'll remember that” alone does not establish a saved update.

If you also change the story direction, separately choose which character records, outline and prose need revision. Make that a scoped task, not a silent whole-book rewrite hidden inside a summary correction.

## 5. Update without accumulating contradictions

- **After accepting a setting:** keep a short decision and relevant file names; remove rejected alternatives without quietly turning “possible” into “happened.”
- **After saving a scene:** update actual progress and its exit state. Planning to hand over the bag next is not its current physical state.
- **Before changing conversations:** leave completed work, open decisions and the next task; check the target file and quotations in the new conversation.
- **After drift:** find which evidence is outdated and correct that specific layer; do not blindly overwrite every file to make them agree.

You do not need to fill every field on a weekly or fixed-chapter schedule. Maintain them when decisions, progress or evidence actually change, rather than copying all chat into Notes. Memory content may enter AI context; it is not a private scratchpad for information you intend to hide from the model.

## Common questions

### Why can a character still drift with memory enabled?

The summary may be outdated, incomplete or conflict with a file; context can be budget-limited, and the model can misunderstand. Check the saved evidence and the key sentence for this turn rather than asking it to “never be wrong.” The distinction between author truth, character knowledge and reader knowledge in the [long-novel continuity guide](https://zenstory.ai/oh-story/long-novel-continuity) is useful, but Oh Story's tracking files are not automatically features of the hosted workbench.

### Is saying “remember this” enough to save it?

Not necessarily. It may remain a chat response, or a project-status update may have occurred. Read the tool result and open memory to check; important formal settings also need clear evidence in project files.

### Does exporting TXT back up memory too?

Do not treat it that way. Manuscript export covers draft and script files, not a complete project backup containing these four fields, materials, chat and history. To keep the summary independently, copy the four fields to your own document. See [version history](https://zenstory.ai/docs/user-guide/version-history) and [manuscript export](https://zenstory.ai/docs/user-guide/export) for their separate recovery boundaries; neither is a promise to recover any arbitrary memory edit.

## Implementation references

Source checked on 2026-09-13. These fixed revisions support the fields, save path and context limits. This tutorial did not operate a live account or run a model to establish writing quality or memory effectiveness.

- [AI Memory entry in the chat header](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/web/src/components/ChatPanel.tsx#L1595-L1609)
- [Save changed fields and report failures](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/web/src/components/ProjectStatusDialog.tsx#L128-L155)
- [Cancel and Save controls](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/web/src/components/ProjectStatusDialog.tsx#L225-L247)
- [Four project fields and character limits](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/config/project_status.py#L12-L40)
- [Read saved project context from the database](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/agent/context/assembler.py#L1022-L1054)
- [Budget-bound long summary and notes fields](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/agent/context/assembler.py#L1319-L1352)
- [Format saved fields as project state](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/agent/context/assembler.py#L1791-L1835)
- [Agent tool can write project status](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/agent/tools/mcp_tools.py#L1609-L1639)
