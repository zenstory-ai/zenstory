# Advanced custom skills

Save writing and revision methods you use repeatedly as custom skills so you do not have to re-explain the rules each turn. A strong skill focuses on one clear problem and states the evidence to read, steps to follow, deliverable format, and edit boundaries.

## Extract instructions from a real task

Run a local task in chat until you are happy with the result, then extract the reusable method into a skill. Keep story-specific character names, private notes, and unpublished manuscript text out of general-purpose skills.

For example, you can place this limited-viewpoint check in the **Instructions** field:

```markdown
# Limited viewpoint check

## Evidence
Read the specified character notes, current scene, and quoted passage.
Ask if the character's knowledge is missing.

## Steps
1. Find assertions about another character's thoughts, identity, or hidden truth.
2. Compare them with what the viewpoint character already knows.
3. Separate contradictions from decisions still open to the author.

## Deliverable
List problem sentences, file evidence, and the smallest replacements.
Retain confirmed actions and outcomes.
Suggest changes in chat first. Do not edit files or invent biographies.
```

A skill supplies the method; supply the current turn's material by opening the target file, highlighting text and clicking **Quote**, or naming files to read in your prompt. Placeholders such as `{{selectedText}}` or `{{currentFile}}` are not automatically replaced with project content.

## Write descriptions, tags, and explicit selection rules

- **Description (tells the AI when to use the skill)**: When creating or editing a skill, the **Description** field (up to 1,024 characters) tells the AI what problem the skill solves and when to load it automatically. Be specific (for example, *“Check a scene for information the viewpoint character should not know yet”*) rather than using vague labels like *“edit prose.”*
- **Tags (for organization only)**: The **Tags** field (comma-separated, up to 50 tags) is only for categorizing and filtering your skills in the Skills list—**tags do not affect when the AI uses the skill**.
- **Explicit selection in chat**: Whenever you want a specific skill applied to a turn, select it directly via `/` in the chat input or click `+` next to the skill in the project's left **Skills** tab (up to **3** skills per message). For multi-stage work, attach different skills for planning, drafting, and revision.

## Add and manage resource files

After saving a custom skill, you can manage attached **Resource files** at the bottom of the edit dialog. Store longer checklists, templates, or reference examples under `references/` or `assets/`, and explain in **Instructions** when each resource file should be read.

| Field or item | Limit |
| --- | --- |
| **Skill Name** | Required, up to 100 characters |
| **Description** | Optional, up to 1,024 characters (used by the AI to decide when to load the skill) |
| **Tags** | Optional, up to 50 tags, 100 characters each (for organizing skills only) |
| **Instructions** | Required, up to 50,000 characters |
| **Resource files** | Up to 20 text files, 64 KiB per file, 256 KiB total |

Resource files support `.md`, `.txt`, `.json`, `.yaml`, `.yml`, and `.csv` text formats under `references/` or `assets/`. Skills provide methods and reference text only; scripts inside packages are never executed.

## Import a standard skill package

Click **Import** in the top right of the **Skills** page to upload a `.md` file (up to **256 KiB**) or a `.zip` archive (up to **1 MiB**). A standard package includes `SKILL.md` (up to 256 KiB) with at least `name` and `description` in its YAML frontmatter:

```markdown
---
name: viewpoint-check
description: Check a scene against the viewpoint character's knowledge.
---

Read the specified scene and character notes.
List inconsistent sentences, evidence, and suggested replacements.
```

A `.zip` package may also include `references/` or `assets/` text resource folders. Check any import warnings after upload: scripts and unsupported binary files are skipped automatically, and `allowed-tools` frontmatter does not alter workbench permissions.

## Test and iterate

Test a new skill on a short passage first, then test how it behaves when evidence is missing and when a fact conflicts with an established character sheet. Inspect whether it pinpoints the right lines and respects your edit boundaries rather than just checking whether the skill was loaded.

If the output misses the mark, adjust the single most relevant instruction first; if a skill grows too long or combines conflicting goals, split it into separate skills. Before clicking **Share to Discover**, export a local backup with **Export as zip** and remove any private or unlicensed text.

See [Using skills](../user-guide/skills.md) for basic skill management.
