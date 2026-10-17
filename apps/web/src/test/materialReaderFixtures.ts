import type {
  MaterialNovel, MaterialCharacter, MaterialStory, MaterialPlot, MaterialStoryLine,
  MaterialCharacterRelationship, MaterialGoldenFinger, MaterialWorldView,
} from '../lib/materialsApi';

const createdAt = '2026-10-17T00:00:00Z';
export const referenceMaterial: MaterialNovel = {
  id: '24', user_id: 'responsive-user', title: '山海故人 · 合成示例', original_filename: 'synthetic.txt',
  file_size: 1000, status: 'completed', chapters_count: 1, characters_count: 1, stories_count: 1,
  plots_count: 1, story_lines_count: 1, relationships_count: 1, golden_fingers_count: 1, has_world_view: true,
  created_at: createdAt, updated_at: createdAt,
};
export const referenceCharacter: MaterialCharacter = {
  id: '101', novel_id: '24', name: '沈砚', archetype: 'protagonist', aliases: ['阿砚', '守灯人'],
  description: '离乡十年的修灯匠，习惯先听完别人的话，再作决定。\n看似克制，却始终放不下失踪的兄长。\n\n**人物动机**：查明旧信的来历，不让故乡再有人无声消失。',
  first_appearance_chapter_id: 1003, first_appearance_chapter: 3, created_at: createdAt,
};
export const referenceStory: MaterialStory = {
  id: '201', novel_id: '24', title: '归乡与旧信', story_type: 'mystery', chapter_range: '1–12',
  synopsis: '沈砚循着一封旧信回到临江城，发现寄信人早已离世。\n他从旧灯铺查起，逐渐发现失踪案与守灯人的约定有关。',
  core_objective: '找到写信的人，确认兄长最后一次出现的位置。',
  core_conflict: '每找到一条线索，就有一位知情人选择沉默。\n保护故人与追索真相，开始成为无法兼顾的选择。',
  themes: '["归乡", "承诺", "真相与代价"]', created_at: createdAt,
};
export const referencePlot: MaterialPlot = {
  id: 301, chapter_id: 1003, index: 0, plot_type: 'REVEAL',
  description: '老人递来的信封上，写着只有兄长才知道的称呼。\n沈砚意识到，这封信并不是在十年前写成的。\n\n窗外的钟声响起，老人第一次避开了他的目光。',
  characters: ['沈砚', '温掌柜'],
};
export const referenceStoryline: MaterialStoryLine = {
  id: 401, novel_id: 24, title: '守灯人的约定',
  description: '以沈砚追查兄长下落为主线，将旧灯铺、临江城和山海关的线索串联起来。\n每次接近真相，都会重新考验他对故人的信任。',
  main_characters: ['沈砚', '温掌柜', '陆青禾'], themes: ['信任', '守约'], stories_count: 4, created_at: createdAt,
};
export const referenceRelationship: MaterialCharacterRelationship = {
  id: 501, character_a_id: 101, character_a_name: '沈砚', character_b_id: 102, character_b_name: '温掌柜',
  relationship_type: 'mentor', sentiment: 'complex',
  description: '温掌柜曾教沈砚修灯，也曾隐瞒兄长离去的原因。\n沈砚依旧愿意信任他，却开始独自核对每一条线索。',
};
export const referenceGoldenFinger: MaterialGoldenFinger = {
  id: 601, novel_id: 24, name: '听灯', type: 'special_ability', first_appearance_chapter_id: 1003,
  description: '触碰旧灯时，可以听见点灯人留下的一段声音。\n能力只能重现片段，无法判断话语的真假。',
  evolution_history: [
    { stage: '初醒', chapter: 3, timestamp: '暴雨之夜', description: '第一次听见兄长留在灯芯里的告别。\n声音很短，随后灯火熄灭。' },
    { stage: '辨声', chapter: 12, description: '能够区分不同点灯人的声音，但仍无法还原完整经过。' },
  ], created_at: createdAt,
};
export const referenceWorldview: MaterialWorldView = {
  id: 701, novel_id: 24,
  power_system: '修灯者以心火感知旧物中的记忆，能力越强，越容易受到他人情绪影响。\n\n- 听声：感知片段\n- 辨影：辨认记忆中的人物\n- 守灯：替他人保留即将消散的记忆',
  world_structure: '临江城位于山海交界，城内保留着新旧两套秩序。\n山海关以外的旧路，只在特定夜晚出现。',
  key_factions: [
    { name: '旧灯铺', description: '为城中居民修灯，也替离去的人保存最后一段记忆。', leader: '温掌柜', territory: '临江城南街' },
    { name: '巡夜司', description: '追查异常灯火与无故失踪的居民。\n与守灯人保持合作，也有所防备。', leader: '陆青禾', territory: '临江城与山海关' },
  ], special_rules: '同一段记忆只能被完整听见一次。\n熄灭的心火不能靠另一盏灯重新点燃。',
  created_at: createdAt, updated_at: createdAt,
};

export const referenceCases = [
  { kind: 'character', folder: '角色', treeTitle: '沈砚', title: '沈砚', text: '离乡十年的修灯匠' },
  { kind: 'story', folder: '剧情', treeTitle: '归乡与旧信', title: '归乡与旧信', text: '沈砚循着一封旧信回到临江城' },
  { kind: 'plot', folder: '情节点', treeTitle: referencePlot.description.slice(0, 50) + '…', title: '情节点', text: '老人递来的信封上' },
  { kind: 'storyline', folder: '故事线', treeTitle: '守灯人的约定', title: '守灯人的约定', text: '以沈砚追查兄长下落为主线' },
  { kind: 'relationship', folder: '关系', treeTitle: '沈砚 - 温掌柜', title: '沈砚 - 温掌柜', text: '温掌柜曾教沈砚修灯' },
  { kind: 'goldenfinger', folder: '金手指', treeTitle: '听灯', title: '听灯', text: '触碰旧灯时' },
  { kind: 'worldview', folder: '世界观', treeTitle: '世界观设定', title: '世界观', text: '修灯者以心火感知旧物中的记忆' },
] as const;
