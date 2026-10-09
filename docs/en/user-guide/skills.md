# Skills System

The Skills System is a core feature of the ZenStory Writing Workbench: it lets you turn a writing method (character design, outline structure, scene description, and so on) into a preset instruction set that the AI applies whenever it's needed.

## What are Skills?

A skill has a name, a description, and instruction content. When a skill is enabled, the AI only sees its name and description at the start of a conversation (like a table of contents). When your request actually needs that skill, the AI reads its full instructions on its own and applies them — there's no fixed phrase you need to say to "activate" it.

Because the AI decides whether a skill applies based on its description, **a clear, specific description matters more than anything else for getting the AI to find and use the right skill.**

**Core Advantages**:
- Turn complex instructions into reusable skills instead of retyping them every time
- Ensure consistency in AI output style
- Customize exclusive skills based on personal habits
- Discover and use quality skills shared by the community
- Explicitly pick a skill for a specific message when you want more control

## Discover Skills

[Screenshot: Discover Skills page, showing skill card grid, category filters, and search box]

Browse and add quality skills from official and community sources on the "Discover Skills" page:

**Functional Operations**:
- **Search**: Enter keywords to quickly find needed skills
- **Category Filter**: Click category tags (Writing, Character, World-Building, Plot, Style) to filter
- **Add Skill**: Click "Add to My Skills" to add the skill to your personal library
- **Expand View**: Click the expand button to view complete instruction content

**Skill Sources**:
| Source | Description |
|--------|-------------|
| Official | Carefully crafted by the ZenStory team, fully tested and optimized |
| Community | Created and shared by users, published after review |

## My Skills

[Screenshot: My Skills page, divided into "Custom Skills" and "Added Skills" sections]

Manage all personal skills, divided into two categories:

| Type | Description | Permissions |
|------|-------------|-------------|
| Custom Skills | Skills you created personally | Editable, deletable, shareable |
| Added Skills | Skills added from public library | Read-only, removable |

**Management Features**:
- Search: Quickly locate skills by name or description
- Batch Operations: Select multiple skills for batch deletion
- Import: Click "Import" at the top and choose a `.zip` skill package or a single `SKILL.md` file (up to 1 MB)
- Export: Click the download icon on a skill card to export it as a `.zip` skill package

## Create Custom Skills

[Screenshot: Create skill form, containing four input fields: name, description, trigger words, instruction content]

### Basic Information

- **Skill Name** (required): A concise and clear name
- **Description** (optional): Explain purpose and usage scenarios — **this is the main signal the AI uses to decide when to use the skill, so write it carefully**

**Writing tips for descriptions**:
- Describe the scenario where the skill applies, not a restatement of its name — e.g. "Use when the user needs a believable motive for an antagonist," not "Character design skill"
- Include keywords or phrasing your requests are likely to use, so the AI can match on meaning
- Avoid vague descriptions (like "a useful writing skill") — the more specific, the better the match

### Trigger Word Settings

[Screenshot: Trigger word input box]

Trigger words are an optional label field for your own organization and search — **they are no longer used to auto-activate a skill.** Whether the AI uses a skill is decided by its name and description. Setting guidelines:
- Separate multiple trigger words with commas
- A few keywords that summarize the skill's purpose are enough, to help you search for it later
- No need to cover every possible phrasing

**Example**: `create character, new character, character card`

### Instruction Content

[Screenshot: Instruction editor]

The full method the AI reads when it decides to use this skill, supports Markdown format.

**Writing Suggestions**:
- Clearly define AI's role and task
- Provide output format requirements
- Use Markdown to structure content

**Example Instruction**:

```markdown
# Character Creation Expert

You are a professional novel character design consultant. Please generate character settings in the following format:

## Basic Information
- Name, age, gender, identity

## Physical Features
Detailed description of the character's appearance.

## Personality Traits
- Core personality, behavioral habits, speech style

## Background Story
Brief description of growth experience and important events.

Please ensure character settings are consistent with the story style.
```

### Resource Files

[Screenshot: Resource files section in the skill edit dialog]

Save the skill first before you can add resource files — text reference material (glossaries, sample outlines, style guides) that the AI reads only when it actually needs it, so it doesn't take up space in every turn.

**Rules**:
- Paths must start with `references/` or `assets/`, e.g. `references/glossary.md`
- Text formats only: `.md` `.txt` `.json` `.yaml` `.yml` `.csv`
- Each file up to 64 KB, up to 20 files per skill, 256 KB total per skill
- Resource files on skills added from the public library are read-only

## Edit and Delete Skills

**Edit**: Click the edit icon on the custom skill card to modify content. Added public skills are read-only and cannot be edited.

**Delete**: Click the delete icon, confirm to permanently delete. Supports batch deletion of multiple skills.

**Note**: Deletion cannot be undone, please proceed with caution.

## Explicitly Selecting a Skill in Chat

Most of the time you don't need to intervene — the AI decides on its own whether a skill applies. But if you want the AI to definitely use a specific skill, you can select it before sending a message, up to 3 at a time:

- Click the **+** button next to a skill in the left sidebar's "Skills" tab or in a skill's detail dialog
- Or type `/` in the chat input to open the skill quick picker, then use the arrow keys and Enter to choose

Selected skills appear as chips above the input box and are cleared automatically after the message is sent — you'll need to select again for the next message.

[Screenshot: Chat input showing selected skill chips above the text box]

## Open Skill Format

ZenStory skills follow the open Agent Skills format (`SKILL.md`), which means:

- **Import**: You can import a `.zip` skill package or a single `.md` file in this format (up to 1 MB). Any executable scripts or non-text files in an imported package are automatically dropped, and the import result lists which files were skipped — **ZenStory never executes any script bundled with a skill.**
- **Export**: You can export your own skills as a `.zip` package for use in other tools that support the Agent Skills format (such as Claude Code).

## Share Skills

[Screenshot: Share skill dialog, including category selection]

After creating valuable skills, you can share them to the public library:

1. Click the share icon
2. Select category (Writing, Character, World-Building, Plot, Style)
3. Submit for sharing

Shared skills will be submitted for administrator review and appear in the public library after approval.

## Skill Usage Statistics

[Screenshot: Skill statistics dialog]

Click "Usage Statistics" to view skill usage for the current project. This counts how many times the AI actually loaded a skill's full instructions (including times you selected it manually) — not simple keyword matches.

**Statistical Indicators**:
- Total uses
- Uses of built-in vs. custom skills

**Time Range**: 7 days / 30 days / 90 days

**Visualization**:
- Most used skills ranking (Top 5)
- Daily usage trend bar chart

## Usage Tips

**Writing descriptions** (most important):
- State in a sentence or two when the skill should be used, not just what it is
- Cover the phrasing and keywords users are likely to type
- Review usage statistics periodically and refine descriptions based on actual results

**Trigger words**:
- Use them as personal search tags — they don't affect whether the AI uses the skill
- 2-5 per skill is enough to help you find it later

**Instruction Writing**:
- Use Markdown for structured organization
- Include examples of expected output
- Continuously optimize based on AI output

**Skill Management**:
- Regularly clean up unused skills
- Use consistent naming conventions
- Share valuable skills promptly

---

By reasonably using official skills, discovering community skills, and creating personal skills, let AI better understand and execute your writing needs. Happy writing!
