# File types

Every file in a ZenStory project has a file type that tells the workbench—and manuscript export—what role that document plays. Editing a file's title renames it, while its underlying file type stays the same. Picking the right type keeps planning notes separate from finished chapters when you export your manuscript.

## File types and export scope

| Type | Typical contents and export behavior |
| --- | --- |
| Outline `outline` | Story premise, volume arcs, chapter beats, and scene plans. Excluded from whole-manuscript export; can be downloaded individually. |
| Draft `draft` | Novel or short-story chapters. Included in **Export whole work (TXT)**. |
| Character `character` | Goals, relationships, backstory, and what each character knows. Excluded from whole-manuscript export. |
| Lore `lore` | Locations, world rules, timelines, and power limits. Excluded from whole-manuscript export. |
| Material `snippet` | Project reference excerpts and reading notes. Excluded from whole-manuscript export. |
| Script `script` | Episodic scenes, action lines, and dialogue. Included in **Export whole script (TXT)**. |
| Folder `folder` | Project directory container. Not exported. |

All content files open in the same plain-text editor—file types do not lock you into rigid form fields.

## Outline example

```text
Scene 1 — Claiming the bag
Goal: Shen claims the lost bag; Lin checks her proof.
Obstacle: Shen names the wrong item inside the inner pocket.
Turn: Lin pauses the handover and asks follow-up questions.
Open thread: Why she got the item wrong, and who the bag is really for.
```

Record settled plot decisions in your outline, mark open options clearly as undecided, and update the outline whenever a finished chapter takes a new turn.

## Character and lore notes

Start with the facts that actively constrain the current chapters instead of writing an exhaustive encyclopedia up front:

```text
Lin He — Lost-and-found attendant
Goal: Finish the handover before closing time.
Rule: Cannot release an item on a verbal claim alone.
Knows: The bag's outside matches Shen's description, but the inner pocket does not.
Does not know: Why Shen named the wrong item.
```

In lore and character sheets, separate what a character currently knows from hidden truths only the author knows so the AI does not leak spoilers into dialogue or narration.

## Materials and scripts

`Materials` (`snippet`) files hold reference excerpts inside a project and can be pinned to chat turns. Full-book AI breakdowns live separately in your account's [Materials Library](../user-guide/materials.md) until you import selected entries into a project.

`Script` (`script`) files hold scene headings, character lists, action beats, and dialogue for short-drama or screenplay projects, and are exported as plain text—ZenStory does not render video from scripts.

## Changing a file's type

ZenStory sets a new file's type automatically from the folder where you create it (or from the creation button you pick in the empty editor), and does not have an in-place type switcher. If prose ended up in an `outline` or `snippet` file by mistake, create a new file under **Drafts** or **Scripts**, copy the text over, wait for **Saved** in the status bar, and then decide whether to keep or delete the old file.

New files start with their own version history. See [File tree and file management](../user-guide/file-tree.md) and [Export your manuscript](../user-guide/export.md).
