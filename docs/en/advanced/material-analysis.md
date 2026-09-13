# Material Analysis: From Reference Passage to Your Own Scene

> Repository snapshot (2026-09-13). Use the [current hosted guide](https://zenstory.ai/docs/advanced/material-analysis) for the web workbench. This page reuses [the served guide source at this revision](https://github.com/zenstory-ai/zenstory/blob/1773af4781996363496f47268cce0e7c640108c8/apps/web/docs/en/advanced/material-analysis.md), retaining the explanation and examples for reading in the repository. The date identifies this page’s source version, not the freshness of other repository documents.

The useful result of material analysis is not a long list of labels such as “decisive,” “kind” or “twist.” It is a writing choice you can explain: **what the character wanted, what information changed their approach, and what the new choice still cost.** In ZenStory Workbench, inspect the analysis, then bring selected items or source passages into chat for a specific writing problem.

This guide reads an original recording-room passage, then applies one craft choice to a different scene about a toy-car practice session. Both examples are editorial fiction, not uploaded material, AI analysis results or generated manuscript output.

## 1. Bring a question, not a request for every possible label

Choose a question such as “How can a character change their goal without delivering a speech about the lesson?” Avoid starting with “Analyze everything good about this book and write me a similar one.” If you have read only a passage, limit your conclusion to it. A claim about a whole character arc needs the relevant earlier and later text.

See the [materials introduction](../user-guide/materials.md) for uploads, task states and viewing results. The following categories can provide leads; their presence does not establish complete extraction or correct interpretation.

| Available content | A useful question | What it cannot establish on its own |
|---|---|---|
| Chapter summaries, plot points and source text | Which action changes the situation? What did the summary omit? | That every setup is included or an unmentioned event never happened. |
| Character names, aliases and descriptions | Which particular choice supports a personality judgment? | That a label explains a lifetime of motives. |
| Plots, stories or storylines and related chapters | How do goal, obstacle, choice and consequence connect? | That more plot points mean better writing, or chapter span measures reading pace. |
| World details, special abilities and their development | Which limitation blocks an action? What changes if it is removed? | That every story needs power levels, or an empty field means no constraint exists. |

Use source text to check an interpretation; use analysis items to locate it. Do not assume every material has a complete visual relationship network or timeline. For empty items, unclear summaries or task errors, establish what was actually read before choosing the next passage.

## 2. Original reference: why does one noise change a decision?

**Reference passage: The Missing Solo**

> Fang Yao edited the old concert recording for the third time. A chair scrape still covered part of her mother's solo. Tonight she was hosting a small listening gathering for her mother; the program called it “Her finest performance.” A friend suggested removing the scrape and splicing in the same bar from its later repetition.
>
> Fang played the two sections back and forth. The later one was slightly faster. After the chair scrape, her mother said softly beside the microphone, “No hurry. Let him get settled.”
>
> Fang undid the splice. She crossed out the program line and wrote “That day, she paused,” then kept the two seconds before the scrape as well.

Keep three kinds of reading note separate:

| Kind | What this passage supports |
|---|---|
| Explicit evidence | The program calls it “Her finest performance”; Fang edits repeatedly and tries the splice her friend proposes; the sections differ in tempo; her mother asks someone to wait; Fang undoes the splice and changes the program. |
| Supported interpretation | The program and editing support reading Fang's initial goal as presenting the finest performance. A noise first treated as a flaw gains meaning through the words that follow. She changes her standard for presenting her mother, rather than fixing the recording. |
| Not established here | That her mother has died, Fang has overcome grief, the audience likes the result, her mother always sacrificed herself, or a splice is technically impossible. |

“Her mother is kind” may be a reading impression, but it does not replace the causal sequence. **The scrape remains, the solo is still obscured, and the gathering's response is not shown.** The new choice does not erase the old cost.

Quote the passage directly, or attach the relevant material item and supply the source text, then ask:

```text
Discuss only the supplied passage The Missing Solo, not an imagined whole book.
Identify Fang's initial goal, the source evidence that changes it,
her actual choice, and the cost that remains.
Support interpretations with short quotations. Separate explicit facts,
interpretation and questions needing more context.
Do not invent biographies, continue the gathering or modify project files.
End with one description of the choice's function that does not mention
recordings, chairs or programs.
```

One possible description: **Specific information changes what “doing this well” means to the character; they change their action while still paying for the abandoned goal.** This is one craft option, not a formula for every twist or character arc.

## 3. Transfer the function, not just the names and props

Changing a recording to a video, a chair scrape to a cough, and then repeating the undone edit and changed title still follows the reference's main event structure. Decide what your new character must actually choose, rather than asking for a copy of the source's rhythm.

Here is a candidate design for a different original story, **A Different Starting Line**. It shares no story world with the reference. These are new author decisions, not facts extracted by material analysis.

- Cen Yi has ten minutes left in his own toy-car practice slot. He wants one uninterrupted full-speed practice run.
- Tao Tao, visiting for the first time, wants to try the controller but says, “I only know how to stop. I don't know how to turn left.” She has not completed a left turn.
- Cen can keep practicing or ask whether she would like to use the remaining slot to try a turn. He cannot finish both tasks in that time.
- This is a fictional practice session, not a description of official competition rules, hazardous operation or prizes.

Both directions can work, but they do different things:

| Candidate | Action | Standard of success and remaining cost |
|---|---|---|
| A: Keep the original goal | Cen continues his full-speed practice and explains that this slot is not a lesson. | His own training remains the goal; Tao gets no practice this time. This can explore boundaries or distance, not necessarily make a bad story. |
| B: Change this session's goal | He asks Tao; once she agrees, he gives her the remaining time and offers guidance without operating for her. | Success becomes her completing one left turn herself. His full-speed run remains unfinished; an unexpected victory does not cancel that cost at the end. |

**This tutorial chooses B.** It transfers the function of revising a success criterion while retaining a cost. The action changes from editing a past record to collaborating with someone present, with feedback from her own action—not merely renamed objects. A different subject does not automatically make the story your own; the specific character choices, event structure and expression still matter.

## 4. Turn the chosen design into prose, not more labels

Choose B before requesting prose. This prompt does not assume those project files already exist:

```text
Use option B for A Different Starting Line to write a 350–500-word candidate scene.
Cen initially wants his own full-speed run, then gives Tao the remaining slot.
Tao must agree to try. Cen must not perform the turn for her.
Show the change through his question, handing over the controller and waiting;
do not add a speech beginning “He finally understood.”
Keep the cost: Tao completes one left turn, but Cen does not complete his own run.
Do not invent prizes, family relationships or permanent mastery.
Do not reuse distinctive sentences from the reference passage.
Return candidate prose in chat only; do not write files or update project memory.
```

“Chat only” is a working instruction, not enforced read-only permission. See the [AI assistant guide](../user-guide/ai-assistant.md) for file-operation boundaries. To draft directly into the project, name the target file and permitted scope first, so “analyze a reference” does not silently become “rewrite my manuscript.”

Here is an **original ending excerpt**, not a full output satisfying that prompt's word count:

> Tao's thumb hovered over the button. “Will you press it for me?”
>
> Cen pulled back his half-outstretched hand. “You try. Slowly first.”
>
> The little car touched the boundary line, backed away, then turned left. The end-of-slot timer sounded. Cen glanced at the full lap he had never run and left the controller in Tao's hands. “That turn was yours.”

The reaching and withdrawing hand shows Cen choosing not to take over. The timer and unrun lap preserve his loss. This does not establish Tao's mastery of every control or announce Cen's permanent transformation. The complete scene still needs to establish his initial goal, her willingness and the time limit; this ending alone cannot supply those foundations.

## 5. Read pacing through how the choice happens

In the reference, replaying, hearing a line, undoing a splice and changing a program serve different functions. Remove the mother's words and the decision loses a support. Explain at the start that Fang never cared about perfection and the later change becomes weaker. Those relationships help this writing task more than counting “two action paragraphs and one dialogue paragraph.”

The car scene needs room for Tao's attempt and Cen resisting the urge to take over. It does not need the reference's paragraph count, sentence lengths or pause locations. Nor is there a universal requirement for a medium conflict every five to ten chapters or a large climax every twenty to thirty. Make those decisions for the particular story, form and information available to its readers.

## 6. Save reading notes separately from story facts

If you want to keep the work, you could create these files yourself. These are suggested names, not automatically generated analysis cards or special commands:

- **Reference reading — A changed goal**: material name, source location, short quotations, interpretations and unread scope.
- **A Different Starting Line — Scene outline**: only accepted design, including consent, the choice and its remaining cost.
- **Scene 1 — The practice slot**: prose you have actually selected. A chat-only candidate does not make “Scene 1 complete” a true memory update.

Displaying material results does not establish an analysis-card editing or export feature. Your reading notes are not a replacement for the source. In the next request, attach the needed item or quotation and name the writing problem; do not assume the whole library, whole book or previous analysis is automatically in every chat turn.

Continue with the [decisions-to-prose workflow](workflow-tips.md) and [project-memory example](ai-memory.md). If you need to reconstruct an unfinished manuscript for continuation rather than study it as a reference, see [Oh Story import and continuation](https://zenstory.ai/oh-story/import-and-continue). That is a separate project's workflow, not another name for the material-upload button.

## Implementation sources

Source checked on 2026-09-13. These pinned references support the result-display and context boundaries. The fictional passages and readings are original editorial examples, not model outcomes established by the code.

- [Loading categories when their folders expand](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/web/src/pages/MaterialDetailPage.tsx#L82-L209)
- [Actual result-display types](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/web/src/components/materials/MaterialViewer.tsx#L123-L198)
- [Plot and storyline descriptions and related chapters](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/web/src/components/materials/MaterialViewer.tsx#L317-L358)
- [Task states and errors at completion](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/services/material/ingestion_jobs_service.py#L55-L105)
- [Explicit material attachments, quotations and context assembly](https://github.com/zenstory-ai/zenstory/blob/306059d9418dbdd6612d67574816ad960f58e51c/apps/server/agent/context/assembler.py#L149-L203)
