# File Types Reference

ZenStory uses a unified file system to manage your creative content. Each file type is optimized for specific creative scenarios, helping you efficiently organize and manage the various components of your novel.

Understanding and correctly using file types allows the AI to better comprehend your creative intent and provide more precise assistance.

---

## Outline

[Screenshot: Outline file example showing hierarchical chapter planning structure]

### Purpose

Outline files are used to plan the overall structure and plot direction of your story - they serve as the blueprint for your creation. They help you:

- **Chapter Planning**: Design chapter divisions and plot arrangements
- **Story Structure**: Organize main plots, subplots, and relationships between plot points
- **Creative Guidance**: Provide a clear framework and direction for AI-generated content

### Recommended Structure

Outline files support hierarchical structures. We recommend the following format:

```
Volume 1: The Beginning
  Chapter 1: Transmigration
    - Protagonist wakes up, finds themselves in another world
    - Initial understanding of the new world's rules
    - Meets the first key character
  Chapter 2: First Encounter
    - First conflict between protagonist and new companion
    - Reveals protagonist's special abilities
    - Plants important foreshadowing

Volume 2: Growth
  Chapter 3: Training
    - ...
```

### Usage Tips

1. **From Coarse to Fine**: Start with an overall architecture outline, then gradually refine to specific plot points for each chapter
2. **Mark Key Points**: Highlight key plot points, turning points, and foreshadowing in the outline
3. **Collaborate with AI**: After selecting an outline file, ask in the AI chat to "generate Chapter 1 content based on this outline"
4. **Continuous Updates**: As writing progresses, update the outline to reflect changes in actual content

---

## Draft

[Screenshot: Draft file editor showing rich text editing, word count, and auto-save status]

### Purpose

Draft files are your core creative space for writing the actual content of your novel. This is the heart of your final output, carrying your story.

### Features

- **Rich Text Editing**: Supports text formatting for a smooth writing experience
- **Real-time Save**: Automatically saves every modification - never worry about losing content
- **Word Count**: Real-time display of word and character counts for easy length control
- **Version History**: Automatically records historical versions of each change, with comparison and rollback support
- **AI Assistance**: Supports AI continuation, rewriting, expansion, and other features

### Usage Tips

1. **Organize by Chapter**: Each draft file corresponds to one chapter or an independent story unit
2. **Review Version History Regularly**: When unsatisfied with changes, you can always revert to previous versions
3. **Use with Outlines**: Plan content in the outline first, then expand writing in the draft
4. **Leverage AI Assistance**:
   - Select draft text and ask AI to help polish and optimize
   - Request AI to continue subsequent content
   - Ask AI to optimize dialogue and scenes based on character cards and world settings

---

## Character

[Screenshot: Character card example showing detailed character settings]

### Purpose

Character cards are used to record detailed settings and profiles for characters in your story. Complete character cards help the AI maintain consistency in character personalities and generate dialogue and behaviors that better fit the characters.

### Recommended Content

A complete character card should include the following information:

#### Basic Information
- **Name**: Character's full name, aliases, nicknames
- **Age**: Actual age or apparent age
- **Identity**: Profession, social status, role positioning (protagonist/supporting/antagonist)

#### Physical Description
- **Facial Features**: Face, eyes, nose, etc.
- **Attire**: Common clothing, signature accessories
- **Distinguishing Marks**: Scars, tattoos, birthmarks, etc.

#### Personality Traits
- **Core Personality**: Main personality characteristics (e.g., calm, lively, sinister)
- **Behavioral Habits**: Catchphrases, habitual gestures
- **Values**: Beliefs, bottom lines, pursuits

#### Background Story
- **Origins**: Family, upbringing environment
- **Key Experiences**: Events that shaped their personality
- **Relationships**: Family relations, friends, enemies

#### Abilities
- **Special Abilities**: Martial arts, magic, skills, etc.
- **Source of Power**: Natural talent, training, fortuitous encounters
- **Limitations**: Weaknesses, costs

### Template Example

```
[Basic Information]
Name: Li Qing
Age: 24
Identity: Inner disciple of Tianjian Sect

[Physical Description]
Tall and slender, with sword-like eyebrows and bright eyes. Often wears azure robes with a long sword at the waist.
A faint scar on the left eyebrow bone from a childhood sword practice accident.

[Personality Traits]
Calm and introverted, not fond of speech, but decisive in action.
Catchphrase: "The sword is in the heart, the person is beyond the sword."
Values promises deeply - once made, will give their all to fulfill it.

[Background Story]
From humble origins, adopted by the Tianjian Sect's sect leader as a child.
Lost a close friend during a trial at sixteen, and has closed off their heart since then.
Has a close relationship with junior martial sister Lin Wan'er, though never admits it.

[Abilities]
Practices the Tianjian Art, has reached the seventh level.
Sword style is sharp and aggressive, specializes in fast sword attacks.
Weakness: Sword intent becomes unstable when emotions fluctuate.
```

### Usage Tips

1. **Cover All Major Characters**: Create character cards for all main characters and important supporting characters
2. **Continuously Improve**: Update character experiences and relationship changes as the story develops
3. **Cross-Reference**: When creating new characters, note their relationships with existing characters
4. **AI-Generated Dialogue**: When writing dialogue scenes, select relevant character cards so the AI generates dialogue that fits each character's personality

---

## Lore

[Screenshot: Lore file example showing power systems, faction distributions, and other settings]

### Purpose

Lore files are used to build the background settings and rule systems of your story. Comprehensive world settings make your story more believable and help the AI maintain logical consistency when generating content.

### Recommended Categories

We recommend organizing lore into the following categories:

#### World Structure
- Overall architecture of the world (single world/multiverse)
- Geography, national distributions
- Detailed introductions to important locations

#### Power System
- Types of abilities (martial arts/magic/technology/superpowers, etc.)
- Ability level classifications
- Methods of cultivation or advancement
- Limitations and costs of abilities

#### Factions
- Major nations and organizations
- Sects, guilds, corporations, and other forces
- Relationships between factions (allies/enemies/neutral)

#### Historical Events
- Important historical milestones
- Major events with far-reaching impacts
- Historical legacy issues

#### Special Rules
- Unique laws governing the world
- Taboos and restrictions
- Social systems and cultural customs

### Usage Tips

1. **Categorize Storage**: Create a "Settings" folder to store different types of settings in separate files
2. **Balance Detail**: Core settings should be detailed; peripheral settings can be brief
3. **Maintain Consistency**: Once established, settings should remain consistent in subsequent writing to avoid contradictions
4. **Update Promptly**: Supplement new setting content as the story develops
5. **AI Reference**: When chatting with the AI, mention or select relevant world settings to help the AI generate content that fits the established lore

---

## File Type Conversion

[Screenshot: Different file types in the file tree, showing type icons]

The system currently does not support direct file type conversion. If you need to change a file's type, we recommend the following approach:

### Manual Content Copy

1. Open the file you want to convert
2. Copy the file content
3. Create a new file in the target type's folder
4. Paste the content into the new file
5. Delete the original file (if needed)

### Notes

- Version history is not preserved after conversion
- Confirm the target type before converting to avoid repeated operations
- Folder names affect the default type for new files:
  - "Outlines" folder -> Outline type
  - "Drafts" or "Manuscripts" folder -> Draft type
  - "Characters" folder -> Character type
  - "Settings" or "Lore" folder -> Lore type

---

## Best Practices

### File Organization Structure

We recommend the following file organization structure:

```
My Novel Project
├── Outlines/
│   ├── Overall Architecture
│   ├── Volume 1 Outline
│   └── Volume 2 Outline
├── Drafts/
│   ├── Chapter 1: Entering Another World
│   ├── Chapter 2: First Encounter
│   └── Chapter 3: Conflict
├── Characters/
│   ├── Protagonist - Li Qing
│   ├── Heroine - Lin Wan'er
│   └── Antagonist - Hei Sha
└── Settings/
    ├── World Structure
    ├── Cultivation System
    ├── Faction Distribution
    └── Historical Background
```

### Tips for AI Collaboration

1. **Explicit References**: Clearly tell the AI which character cards and world settings to reference
2. **Select As Needed**: You don't need to reference all settings every time - just select the relevant ones
3. **Iterative Refinement**: AI suggestions may not fully meet expectations - you can modify and improve upon them
4. **Keep Updated**: Update character and world settings promptly so AI output stays synchronized with story development

By using different file types appropriately, you can help ZenStory better understand your creative intent and provide more precise assistance, making writing more efficient and enjoyable.
