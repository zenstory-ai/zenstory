# Writing workflow

At the end of each turn, save the prose and decisions you accept into your project files and note where the next turn should pick up. Whether you work scene by scene, chapter by chapter, or across a standalone short story, stating your deliverable and evidence files keeps your work consistent.

## Choose this turn's deliverable

| Stage | What to ask for in chat | What to keep in your project at the end |
| --- | --- | --- |
| **Explore** | Compare two or three story directions and endings | One chosen direction and a list of open questions |
| **Plan** | Organize character goals, obstacles, and scene turns | Saved outline (or concept) file and essential character sheets |
| **Draft** | Write a named scene or chapter from your approved outline | Saved prose in the draft file with a clear stopping point |
| **Revise** | Fix a specific issue against your settings or quoted text | Reviewed prose edits and any matching setting updates |

When a character's motive is still unclear, settle the motive in chat before drafting prose. When a full chapter beat sheet is already solid, you can draft the chapter directly without slicing it into arbitrary word-count fragments.

## Keep established decisions in project files

Characters, worldbuilding, outlines, drafts, and reference materials each have their own folder in the left file tree. Keep external reading notes in **Materials**, put approved plot beats in **Outlines** (**Concept** in short stories, **Episode Outlines** in short-drama scripts), and mark undecided alternatives clearly as open. Use **AI Memory** for a concise summary, Writing Conventions, and your current phase.

Before sending a prompt, save any recent manual edits in the editor, keep the target file open, and name the setting or chapter files the AI should read. For local fixes, highlight the passage and click **Quote** (`Ctrl/Cmd + Shift + Q`). Avoid leaving critical plot or character decisions buried only inside a long chat history.

## Example: continuing from Scene 1 to Scene 2

In *Before Closing*, Scene 1 ends saved at the moment Shen Min names the wrong inner-pocket item. Before drafting Scene 2, decide whether the scene will press her on the item or check another proof of ownership, add that decision to your outline, and then request the draft:

```text
Read the current characters, Three-scene outline, and Scene 1.
Scene 2 advances the basis of the claim, not the reason for the mistake.
Shen describes the bag's patch before Lin checks it, then says “The bag isn't mine.”
Use Lin's limited viewpoint. Keep the bag at the desk.
Write into Scene 2 — The check; do not continue to Scene 3.
```

The patch on the bottom of the bag and Shen's final line are new plot decisions—confirm them in your outline so later scenes stay aligned.

## Fix knowledge boundaries before polishing phrasing

If a sentence turns a character's suspicion into omniscient certainty, fix the knowledge boundary first with **Quote** before polishing rhythm or running **Humanize**. Revise only the problem passage while keeping approved actions and outcomes intact.

When the plot direction itself changes, review the affected character files, outline, and earlier chapters explicitly; a phrasing polish should never quietly alter your ending.

## Record progress and back up at milestones

Before wrapping up a session or clicking **New chat**, record four brief lines in **AI Memory → Current Phase** or at the bottom of your outline:

```text
Complete: Scene 2 saved. The patch was checked; the bag remains at the desk.
Open: Shen's relationship to the owner, reason for the mistake, final recipient.
Next: Discuss Scene 3's choice before drafting.
Evidence: Characters, current Three-scene outline, and the first two scenes.
```

Mark a scene complete only after the prose is written to the file and the editor status bar shows it as saved. After an interruption, note the stopping point, read the saved draft first, and fill only the missing section. At key milestones, inspect **History** in the status bar and click **Export whole work (TXT)** (or **Export whole script (TXT)**) to keep an independent local backup.

Related: [Write your first short story](../getting-started/first-project.md), [Using the editor](../user-guide/editor.md), and [Version history](../user-guide/version-history.md).
