"""English names and summaries for official skills, keyed by the stored (Chinese) skill name.

Official skill rows live in the database in Chinese and are matched by name so ids, counters and
user collections stay untouched. Only the name and description shown to English readers change;
the instructions the model loads are not translated. The table follows the live catalog (14 skills
after the 2026-10-21 revision) and must change in the same batch as any catalog change (AGENTS.md);
an official skill missing here falls back to its stored text.
"""

OFFICIAL_SKILL_EN: dict[str, tuple[str, str]] = {
    "续写正文": (
        "Continue the Draft",
        "Write long-form prose: a new chapter from the detailed outline, the next stretch after the last "
        "chapter, or an expanded passage or scene. It checks the characters' state and earlier text first and "
        "looks over its own work for AI tone afterwards. Use it when you ask to write chapter 5, keep going, "
        "expand this part, or write out a scene or a stretch of dialogue.",
    ),
    "世界观设定": (
        "Build the World",
        "Design or fill in your world: power and training systems, special abilities, system or rebirth rules, "
        "factions and institutions, geography and history, and check the setting for contradictions and holes. "
        "Limits, resources and costs make the setting serve characters' choices instead of piling up lore. Use it "
        "when you ask to build a world, design a power system, or check whether the rules hold together.",
    ),
    "创建大纲": (
        "Create an Outline",
        "Plan the overall outline, volume outlines and chapter-by-chapter outlines, linking goals, obstacles, the "
        "protagonist's choices, costs and consequences into a causal chain, with conflicts and twists. It plans "
        "only the span you ask for and writes no prose. Use it when you ask for an outline, what happens next, "
        "how to arrange a run of chapters, or more conflict or a twist.",
    ),
    "创建角色": (
        "Create a Character",
        "Design or deepen characters (protagonists, supporting cast, villains, ensembles and their relationships) "
        "and name people, factions and places. It reads your existing setting and text first and keeps confirmed "
        "facts and each character's voice. Use it when a character feels flat, side characters fade, everyone "
        "sounds the same, or you need names.",
    ),
    "润色修改": (
        "Polish Prose",
        "Fix the language without touching the plot: grammar and unclear references, wordiness and repeated "
        "explanation, flow and rhythm, consistency with the book's style, or the specific edits you name. Use it "
        "when text reads awkwardly, runs long or drifts from the earlier style. For AI tone, use Remove AI Tone.",
    ),
    "节奏控制": (
        "Control Pacing",
        "Diagnose and adjust the speed, density and tension of a chapter or passage: find where it drags, pads, "
        "stays flat, holds back too long or underdelivers at the climax, then propose cutting, expanding, moving "
        "or adding breathing room. A diagnosis is a report only; files change when you ask. Use it when the "
        "pacing feels too slow, too fast or tiring to read.",
    ),
    "钩子设计": (
        "Design Hooks",
        "Design hooks at chapter ends, chapter openings or inside a passage that make readers want to keep going: "
        "pay off this chapter's result first, then grow the next pressure out of it, choosing the kind of hook "
        "by the chapter's mood and the story's stage. Quiet or literary endings don't get a forced cliffhanger. "
        "Use it when an ending falls flat, an opening doesn't grab, or you want suspense here.",
    ),
    "开头生成": (
        "Write an Opening",
        "Write, diagnose or rewrite an opening: a novel's first chapter or first three chapters, a short story's "
        "opening, or a short drama's first episode, with a title and blurb if you like. It starts from the "
        "story's promise and the character's present situation, without forcing a twist into the first lines. "
        "Use it when you need a first chapter, a stronger hook, or title and blurb ideas.",
    ),
    "短篇写作": (
        "Write a Short Story",
        "Take a short story from idea to finished draft: settle the feeling and appeal readers should leave "
        "with, build the core frame and a section outline, then write in batches and check the result, saving "
        "each step to project files and waiting for your go-ahead. Use it when you want a short story, a piece "
        "to submit, an outline for one, or a check of a finished short.",
    ),
    "去AI味": (
        "Remove AI Tone",
        "Find and remove AI tone and template feel in your prose at three levels: story logic, narration and "
        "language. Checking only gives a report without touching the text; asking for changes makes targeted "
        "replacements. Works for novels, short stories and short-drama scripts. Use it when text feels "
        "AI-written, formulaic or fake, or when you paste an AI-detection report.",
    ),
    "短剧改编分析": (
        "Short-Drama Adaptation Analysis",
        "Before adapting a novel, web serial, old draft or your project's world into a short drama: rank the "
        "payoff moments by what viewers get, judge passage by passage what goes straight on screen, what needs "
        "another device and what only works on the page, set an adaptation contract (fixed, swappable, cuttable, "
        "undecided) and compare openings for episode 1. Use it when you ask whether a story can become a short "
        "drama, or want its selling points and opening hook.",
    ),
    "短剧故事概要生成": (
        "Short-Drama Synopsis",
        "Develop a short drama's story and synopsis, adapted or original: compare a few directions built on "
        "different mechanisms, then settle the conflict engine, why viewers side with the protagonist, and the "
        "limits and cost of any special power or rebirth, and write a synopsis sized to the episode count with "
        "the pressure between characters. Use it when you ask for a synopsis, an overall outline or a few "
        "directions to choose from.",
    ),
    "短剧分集集纲生成": (
        "Short-Drama Episode Outlines",
        "Break a short drama's synopsis or overall outline into episode outlines: each episode's starting state, "
        "goal and opposition, turn, payoff, outgoing pressure and handoff facts, with the hook growing out of "
        "that episode's result. It compares three openings for episode 1 and plans paywall episodes when you "
        "name them. Use it when you ask to split the story into episodes or continue with the next batch.",
    ),
    "短剧剧本正文生成": (
        "Write Short-Drama Episodes",
        "Turn short-drama episode outlines into shootable scripts, or revise a script from review notes or your "
        "comments: scene headings, action, dialogue and narration, voice-over, sound effects and on-screen text. "
        "The first beat is the hook, what can be acted is not said, and runtime is estimated from speech and "
        "action. Use it when you ask for an episode script, the next episode, or tighter dialogue or pacing.",
    ),
}

def localized_skill_text(
    *, source: str | None, name: str, description: str | None, language: str | None
) -> tuple[str, str | None]:
    """(name, description) for the reader: English for official skills when the reader uses English."""
    if source != "official" or not (language or "").lower().startswith("en"):
        return name, description
    english = OFFICIAL_SKILL_EN.get(name.strip())
    return english if english else (name, description)


def official_names_matching(search: str) -> list[str]:
    """Stored names of official skills whose English name or summary contains the search text."""
    needle = search.strip().lower()
    if not needle:
        return []
    return [
        stored
        for stored, (english_name, english_description) in OFFICIAL_SKILL_EN.items()
        if needle in english_name.lower() or needle in english_description.lower()
    ]
