# Browser writing workflow: decisions, prose and a clear next step

> Repository snapshot (2026-09-13). Use the [current hosted guide](https://zenstory.ai/docs/advanced/workflow-tips) for the web workbench. This page reuses [the served guide source at this revision](https://github.com/zenstory-ai/zenstory/blob/1773af4781996363496f47268cce0e7c640108c8/apps/web/docs/en/advanced/workflow-tips.md), retaining the explanation and worked example for reading in the repository. The date identifies this page’s source version, not the freshness of other repository documents.

In ZenStory, a writing turn should leave **a result you can accept or reject, plus a clear place to continue**. That result may be a scene, a chapter or an agreed complete short story; fixed-size fragments are not compulsory. State the goal, evidence, permitted changes and stopping point instead of assuming “write the whole book” settles every creative decision.

This is an author's guide to the [hosted ZenStory workbench](https://app.zenstory.ai/dashboard), not an enforced three-stage pipeline or a measured time-saving claim. Follow the [quick start](https://zenstory.ai/docs/getting-started/quick-start) to create a project. To control the first request, leave the idea empty and discuss it after creation.

## 1. Choose the deliverable before mixing discussion, drafting and file changes

| Current situation | Useful request | What ends the turn |
| --- | --- | --- |
| Motive or plot direction is undecided | A few genuinely different options and tradeoffs | The author can choose; alternatives have not become established facts |
| Direction is clear and evidence saved | A named scene or complete chapter draft | The agreed change occurs, without spilling into the next chapter |
| A passage breaks character or causality | The original line, evidence and local replacement | The issue is repaired without changing unrelated facts |
| A writing turn has ended | Completed work, open decisions and the next step | The record describes saved results, not the earlier plan |

If a chapter's goal, choices and ending are settled, you can request the whole chapter at once. Do not force four turns just to satisfy a tutorial. If the obstacle is why a character acts, settle that decision first; more prompts cannot substitute for it. Length is a creative target, not a guarantee of completeness or quality.

## 2. Supply the evidence this turn needs, not every open file

Keep settings, characters, outlines, prose and reference material in suitable files. Use recognizable titles and select file types for their purpose. You do not have to complete every character's age, appearance and catchphrase before writing the first scene.

| Content | Use |
| --- | --- |
| Accepted character and setting facts | Keep them in the relevant files and name the parts to read this turn |
| Outline or scene plan | Record goal, resistance, choice and exit state; separate open questions |
| Draft or script | Store adopted narrative prose, not review notes mixed into the text |
| Reference material | Select relevant, authorized items through the supported attachment controls |
| AI Memory | Use the four project fields for brief context, style, actual progress and open decisions |

**AI Memory is not a required `AI Memory.md` file in the tree.** Creating a file with that name does not bind it to the four memory fields. See the [AI Memory guide](https://zenstory.ai/docs/advanced/ai-memory) for those fields.

Save important changes before sending, open the target draft, name the outline and character files to read, and quote crucial passages when needed. Focused files, attachments and retrieval can contribute context, but opening several files in the background does not guarantee all their text reaches the AI. Context has a budget. Not every file type has the same attachment control as a material item. See the [AI assistant guide](https://zenstory.ai/docs/user-guide/ai-assistant).

## 3. Original continuation exercise: turn scene two into a concrete task

This continues **Before Closing** from the [first-story tutorial](https://zenstory.ai/docs/getting-started/first-project); it is **not a model-run record**. Assume scene one is saved: Shen Min described an inner-pocket item incorrectly; the bag remains at Lin He's lost-property desk and has not been released. Lin does not know the reason. The author has not chosen Shen's relationship to the owner or the eventual recipient.

### Decide which question this scene advances

One route continues investigating the wrong description. Another turns to Shen's basis for claiming the bag, leaving the reason for the error unresolved this scene. They change plot direction, not just the wording of “make it tense.” If you have not chosen, request:

```text
Read Scene 1 — The claim and Three-scene outline first.
Compare two scene-two routes: one advances the reason for the mistaken description;
the other advances the basis for claiming the bag.
For each, state the new information, the character's choice and the question left open.
Give proposals in chat only. Do not draft prose or update characters, outline or AI Memory.
```

If you already have a direction, do not reopen alternatives just to follow a template. This exercise selects the second route and **proposes the following new design for author acceptance**. These are not facts already in scene one or preinstalled project content:

| New writing decision | Status and boundary |
| --- | --- |
| A blue patch is on the bottom of the bag | A new prop detail for this exercise |
| Shen describes it before Lin checks it out of Shen's sight | A planned action; do not first show Shen the bottom and then treat her answer as independent information |
| At the end, Shen says “The bag isn't mine” | The chosen dialogue endpoint, not verified knowledge of the mistaken description or true relationship |
| The bag stays at the desk; no owner arrives | This turn's selected exit boundary, not the final ownership decision |

This separates knowing a detail from authority to collect the bag. A matching patch description does not establish that Shen is its owner, a liar or an authorized representative. These are fictional story choices, not real lost-property procedures.

### After acceptance, make the prose implement the decision

Explicitly add the accepted design to Three-scene outline and the relevant setting record, keeping progress marked “scene two unwritten.” Create or select the draft-type target **Scene 2 — The check**, then request:

```text
Read Lin He, Shen Min, Three-scene outline and the saved scene-one prose first.
The target draft is Scene 2 — The check. If titles are duplicated, list candidates first.

Use the accepted blue-patch design: Shen gives its detail before seeing the bottom;
Lin checks it and asks what entitles Shen to claim the bag.
End with Shen saying “The bag isn't mine.”
Write the scene in Lin's limited viewpoint, targeting 400–600 English words.
Do not release the bag, explain the earlier error, introduce the owner arriving,
or add hidden character truths. Stop at that line's landing; do not write scene three.

Write the adopted draft into the named target, not a duplicate file.
Do not change scene one, motives or the final recipient.
Identify missing essential evidence before inventing it.
```

This request explicitly permits writing the target file. If you want to see a candidate first, replace the final paragraph with “Give the draft in chat only; do not change files or memory,” then adopt it manually. That is a task boundary, not enforced read-only access. Agent file operations do not all guarantee per-change approval dialogs.

## 4. Repair a line without reselecting the whole story

First read for the agreed changes: what the characters know, where the bag is and which line has been spoken. Then judge pacing and language. You do not have to reassess every craft dimension in every turn.

**Suppose the draft says:**

> The patch description matched. Lin finally knew Shen was entitled to take the bag.

The problem is not insufficient vividness: familiarity with a detail has become collection authority. A possible local replacement is:

> The patch matched, but the wrongly described item in the inner pocket remained unexplained. Lin did not pass the bag over.

This is an original replacement example, not an applied edit. It keeps the matching description and unreleased bag without deciding Shen's true identity. Quote the actual problem passage, ask to repair only that inference, and preserve the accepted dialogue and exit state. Do not quietly make scene one's wrong description correct to erase the unresolved issue the story intentionally retains.

See the [editor guide](https://zenstory.ai/docs/user-guide/editor) for quotations, diff actions and save status. Changing an identity or ending is a separate plot revision: name the affected outline, character and prose files rather than assuming they synchronize automatically.

## 5. What do saving, versions and export each protect?

- **Saving prose:** writes current text to the file. Check save status; autosave does not guarantee that disconnection, conflict or closing the page cannot lose unsaved input. Avoid simultaneous overwrites while the AI edits that file.
- **File versions or project snapshots:** support comparison and recovery under creation conditions and version allowances. Prose can save successfully without a new snapshot. Do not assume every edit is retained forever, or roll back a whole project just to recover one line. See [version history](https://zenstory.ai/docs/user-guide/version-history).
- **Manuscript TXT:** combines undeleted `draft` and `script` files, including titles and prose. Ordering uses chapter/episode sequence and ordering information, not just creation time. It does not contain complete characters, materials, chat, memory or history. Keep separate copies of other important material; see [manuscript export](https://zenstory.ai/docs/user-guide/export).

These operations are not interchangeable and do not guarantee that work can never be lost. This guide does not ask you to delete files or roll back an existing project to test recovery.

## 6. Leave the next turn an accurate starting point

Change “planned” to “completed” only after the actual prose contains the agreed result and is saved. This card assumes scene two really finished within the exercise's scope; otherwise record the actual state instead of copying it:

```text
Completed: Scene 2 — The check is saved. The blue patch was checked;
Shen said “The bag isn't mine.” The bag remains at the desk, not released.
Undecided: Shen's relationship to the owner, the earlier mistake and final recipient.
Next: discuss how scene three addresses the basis for the claim; do not draft yet.
Evidence: current character files, Three-scene outline and the saved first two scenes.
Do not: treat Shen's statement as a verified identity finding or repeat the same check.
```

If only a chat draft exists, say “candidate supplied, not yet adopted.” If interruption left only part saved, name the stopping point. A new conversation or Stop button does not undo file changes that already happened. Read the current draft before continuing the missing portion.

## 7. Adapt the method to long and short fiction

**Serial fiction:** keep the distant direction, expanding decisions into detail where they support the current writing. Plan longer-range setups when needed. There is no compulsory batch of ten to twenty chapters. When feedback changes direction, distinguish written facts from future plans before choosing the revision scope.

**A complete short story:** if the central question, choices and ending are clear, request a full draft. If only the opening is known, first settle the decisions that affect its ending. No fixed six-day schedule, word count or endless sentence-level polishing is required.

Whatever rhythm you choose, leave usable prose or an accepted creative decision—not just more alternatives and longer chat. Use the focused guides and actual saved results to understand the workbench's boundaries.

## Implementation references

Source checked on 2026-09-13. These fixed revisions support context, save, tool and export behavior. The original workflow is not an executed model case or evidence of faster writing or manuscript quality.

- [Focused file, attachments and quotations in a request](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/web/src/components/ChatPanel.tsx#L1151-L1177)
- [Context is selected from explicit inputs and retrieval](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/agent/context/assembler.py#L149-L203)
- [Ordinary saves and version-creation choices](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/web/src/components/SimpleEditor.tsx#L509-L554)
- [Saving prose can succeed without a new version](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/api/files.py#L900-L960)
- [Exported file types and ordering](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/services/features/export_service.py#L110-L143)
- [TXT contains titles and prose](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/services/features/export_service.py#L197-L203)
- [Agent file-operation dispatch](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/agent/tools/file_ops/router.py#L86-L119)
