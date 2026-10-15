# Advanced custom skills

Save methods you use repeatedly so you do not explain them every time. A skill works best on one clear problem with evidence, steps, a deliverable, and boundaries.

## Start from a real task

Complete a useful local task in chat, then extract its method. Keep private manuscripts and story-specific names out of a general-purpose skill.

Use this limited-viewpoint method in the instructions field:

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

A skill provides a method. Supply this turn's material by opening the target, quoting text, or naming files to read. `{{selectedText}}` and `{{currentFile}}` are not automatically replaced with project content.

## Trigger phrases and explicit selection

Use memorable, task-specific phrases such as “check viewpoint” and “limited viewpoint.” Separate them with English commas. Avoid broad words such as “edit” for every skill.

Select a skill explicitly through `/` when you want it used. Choose up to 3 per turn; use different methods at different stages for complex tasks.

## Add resources

After saving a skill, maintain longer templates and reference text under `references/` or `assets/`. Explain in the instructions when each resource should be read.

| Field | Limit |
| --- | --- |
| Name | 100 characters |
| Description | 1,024 characters |
| Instructions | 50,000 characters |
| Trigger phrases | Up to 50, 100 characters each |
| Resources | Up to 20, 64KiB each, 256KiB total |

Resources support text formats including Markdown, TXT, JSON, YAML, and CSV. Skills provide methods and reference text; package scripts do not run.

## Import a standard package

Import accepts Markdown or ZIP. A standard package contains `SKILL.md` with at least `name` and `description` in its header:

```markdown
---
name: viewpoint-check
description: Check a scene against the viewpoint character's knowledge.
---

Read the specified scene and character notes.
List inconsistent sentences, evidence, and suggested replacements.
```

ZIP packages must be at most 1MiB and may include text-resource directories. Check import warnings: scripts and unsupported binaries are skipped, and `allowed-tools` does not change workbench permissions.

## Check and improve

Try a short passage, then missing evidence and a conflicting fact. Inspect problem detection and edit scope, not just whether the skill was selected.

Change the most relevant instruction first. Split long or conflicting methods into separate skills. Before sharing, export your own copy and remove private or unlicensed text.

See [using skills](../user-guide/skills.md) for basic operations.
