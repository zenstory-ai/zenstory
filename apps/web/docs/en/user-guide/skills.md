# Using skills

A skill stores a reusable writing method—such as viewpoint checking, character development, scene pacing, or local revision. Pick a skill suited to your task, then supply the characters, source text, and specific requirements in chat.

## Add and use a skill

1. Open **Skills** in the top dashboard navigation. In the default **Discover** tab, search by name or filter by category pills to browse official and community skills.
2. Read the skill card's name and description (on community skills, click **Expand instructions** to read the published instructions), then click **Add to My Skills** to add it to your skills (the button updates to **Added**).
3. Inside a project, type `/` in the AI chat input to select the skill from the popup menu, or open the left **Skills** tab and click `+` next to the skill.
4. Describe your task and target files in the input box and send.

You can explicitly attach up to **3** skills per turn. Even when you do not attach a skill manually, the AI can load enabled skills as needed based on each skill's **Skill Name** and **Description**.

Official skills display their name and description while keeping internal instructions private; once added, they work directly in chat. Community skills are shared by authors and reviewed before listing—click **Expand instructions** on a Discover card to read its instructions, or inspect its instructions and attached resource files in **My Skills** after adding it.

## Manage your skills

Switch to the **My Skills** tab on the **Skills** page to manage your skills by source:

| Source | Available actions |
| --- | --- |
| **Created by you** | Toggle enabled or disabled, **Edit** (name, description, tags, instructions, and resources), **Export as zip**, **Share to Discover** (or unshare), and **Delete** |
| **Added from Discover** | Toggle enabled/disabled, inspect details (full instructions for community skills; purpose summary for official skills), and remove from My Skills |

Added official skills cannot export their instructions or resource files. To customize a workflow, create your own skill with your own instructions.

## Create a custom skill

Click **Create Skill** in the top right of the **Skills** page and fill in the dialog fields:

- **Skill Name** (required, up to 100 characters): A concise name for the method.
- **Description** (optional, up to 1,024 characters): Tells the AI when to use this skill when deciding whether to load it automatically.
- **Tags** (optional, comma-separated, up to 50 tags of 100 characters each): For organizing and filtering your skills only—**tags do not affect when the AI uses the skill**.
- **Instructions** (required, up to 50,000 characters): The evidence to read, steps to follow, deliverable format, and boundaries.

Start with one specific writing problem:

```text
Skill Name: Limited viewpoint check
Description: Check a scene for information the viewpoint character should not know yet.
Tags: viewpoint, revision

Instructions: Read the specified character notes and quoted passage first.
List each problem sentence, its evidence, and the smallest replacement.
Distinguish author knowledge from character knowledge. Ask when evidence is missing.
Suggest changes first; do not edit files or add character background.
```

Save the skill, attach it in a project chat, and provide the passage to check. Free accounts can create up to **3** custom skills, and Pro accounts up to **20**; deleting an unused custom skill frees a slot.

## Import, export, and share

- **Import a skill**: Click **Import** in the top right of the **Skills** page and choose a `.md` file (`SKILL.md` with YAML frontmatter, up to 256 KiB) or a `.zip` package (up to 1 MiB). Inspect the imported name, description, instructions, and resources before using it.
- **Export a skill**: Open a custom skill you created in **My Skills** and click **Export as zip** to download its `SKILL.md` and resource files as a local archive.
- **Share to Discover**: Click **Share to Discover** on an owned skill to submit it for review; once approved, it appears in the public **Discover** catalog. Before sharing, remove private manuscripts, personal details, and any text you do not have the right to publish.

See [Advanced custom skills](../advanced/skill-creation.md) for instruction structure, `references/` and `assets/` resource files, and package limits.
