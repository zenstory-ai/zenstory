import type { Page } from '@playwright/test';
export const responsiveUser = { id: 'responsive-user', username: 'responsive-user', email: 'responsive@example.com', email_verified: true, is_active: true, is_superuser: false, created_at: '2025-01-01T00:00:00Z' };
export const responsiveProjects = Array.from({ length: 10 }, (_, i) => ({ id: `responsive-project-${i}`, name: i ? '小屏检查：很长的项目名称与连续章节标题' + i : '小屏布局回归项目', project_type: 'novel', description: '本地布局检查数据', created_at: '2025-01-01T00:00:00Z', updated_at: '2026-10-04T00:00:00Z' }));
const tree = [{ id: 'folder-drafts', title: '正文', file_type: 'folder', parent_id: null, order: 0, metadata: { folder_type: 'draft' }, children: Array.from({ length: 25 }, (_, i) => ({ id: 'draft-' + i, title: `第${i + 1}章 很长的标题和人物的选择`, file_type: 'draft', parent_id: 'folder-drafts', order: i, metadata: null, children: [] })) }];
const quota = { ai_conversations: { used: 0, limit: 10 }, projects: { used: 1, limit: 3 }, material_decompositions: { used: 0, limit: 5 }, skill_creates: { used: 0, limit: 3 }, inspiration_copies: { used: 0, limit: 10 } };
const ent = { writing_credits_monthly: 10000, agent_runs_monthly: 20, active_projects_limit: 3, context_tokens_limit: 4096, materials_library_access: true, material_uploads_monthly: 5, material_decompositions_monthly: 5, custom_skills_limit: 3, inspiration_copies_monthly: 10, export_formats: ['txt'], priority_queue_level: 'standard' };
const catalog = { version: 'responsive', tiers: [{ id: 'free', name: 'free', display_name: '免费版', display_name_en: 'Free', price_monthly_cents: 0, price_yearly_cents: 0, entitlements: ent, summary_key: 'starter', target_user_key: 'explorer' }, { id: 'pro', name: 'pro', display_name: '专业版', display_name_en: 'Pro', price_monthly_cents: 4900, price_yearly_cents: 39900, entitlements: ent, summary_key: 'creator', target_user_key: 'daily_writer' }] };
const messages = [{ id: 'message-1', role: 'assistant', content: '', created_at: '2026-10-04T08:21:00Z', metadata: null, tool_calls: JSON.stringify(Array.from({ length: 9 }, (_, i) => ({ id: 'tool-' + i, name: i % 2 ? 'query_files' : 'create_file', arguments: { title: '第54章 消失的第五个人' }, status: 'success', result: i % 2 ? [{ id: 'draft-1', title: '第54章 消失的第五个人', file_type: 'draft' }] : { id: 'draft-1', title: '第54章 消失的第五个人', file_type: 'draft' } }))) }];
const material = { id: 'responsive-material', user_id: responsiveUser.id, title: '素材标题：很长的人物选择与情节发展', original_filename: 'source.txt', file_size: 12000, status: 'completed', total_chapters: 25, chapters_count: 25, characters_count: 1, story_lines_count: 0, golden_fingers_count: 0, has_world_view: false, created_at: '2025-01-01T00:00:00Z', updated_at: '2026-10-04T00:00:00Z' };
const skill = { id: 'responsive-skill', name: '长标题技能：人物行动与剧情的因果关系', description: '用具体行动检查角色选择，不覆盖原有设定。', triggers: ['人物'], instructions: '# 角色行动\n保持作者意图，检查行动和后果。', source: 'user', is_active: true, created_at: '2025-01-01T00:00:00Z', updated_at: '2026-10-04T00:00:00Z' };
const publicSkill = { ...skill, id: 'responsive-public-skill', source: 'official', category: 'Writing', tags: ['角色'], author_id: null, status: 'approved', add_count: 42 };
const stats = {
    total_word_count: 12000,
    words_today: 1200,
    words_this_week: 4800,
    words_this_month: 12000,
    chapter_completion: {
        completion_percentage: 50,
        completed_chapters: 2,
        in_progress_chapters: 1,
        not_started_chapters: 1,
        total_chapters: 4,
        chapter_details: [
            {
                outline_id: 'outline-1',
                draft_id: 'draft-1',
                title: 'Chapter 1',
                status: 'complete',
                word_count: 1500,
                target_word_count: 1500,
                completion_percentage: 100,
            },
            {
                outline_id: 'outline-2',
                draft_id: 'draft-2',
                title: 'Chapter 2',
                status: 'in_progress',
                word_count: 800,
                target_word_count: 2000,
                completion_percentage: 40,
            },
            {
                outline_id: 'outline-3',
                draft_id: null,
                title: 'Chapter 3',
                status: 'not_started',
                word_count: 0,
                target_word_count: 1500,
                completion_percentage: 0,
            },
        ],
    },
    streak: {
        current_streak: 3,
        longest_streak: 9,
        streak_status: 'active',
        days_until_break: 1,
        streak_recovery_count: 1,
        last_writing_date: '2026-04-06T00:00:00Z',
    },
    ai_usage: {
        current: {
            total_messages: 14,
            total_sessions: 2,
            active_session_id: 'session-1',
            last_interaction_date: '2026-04-07T03:00:00Z',
            last_interaction_at: '2026-04-07T03:00:00Z',
        },
        today: { total: 3, estimated_tokens: 500 },
        this_week: { total: 10, estimated_tokens: 2000 },
        this_month: { total: 22, estimated_tokens: 4500 },
    },
};
export async function mockResponsiveApp(page: Page, saved: number | null = null, tier: 'free' | 'pro' = 'free') {
    await page.addInitScript(({ user, saved }) => {
        localStorage.setItem('zenstory-language', 'zh');
        localStorage.setItem('user', JSON.stringify(user));
        localStorage.setItem('access_token', 'local-responsive-fixture');
        localStorage.setItem('refresh_token', 'local-responsive-fixture');
        localStorage.setItem('auth_validated_at', String(Date.now()));
        if (saved !== null)
            localStorage.setItem('zenstory_chat_input_panel_height_px', String(saved));
    }, { user: responsiveUser, saved });
    await mockResponsiveApi(page, tier);
}
export async function mockResponsiveApi(page: Page, tier: 'free' | 'pro' = 'free') {
    await page.route('**/api/**', async (route) => {
        const path = new URL(route.request().url()).pathname;
        let data: unknown = [];
        if (path === '/api/auth/me')
            data = responsiveUser;
        else if (path === '/api/v1/projects')
            data = responsiveProjects;
        else if (path.endsWith('/file-tree'))
            data = { tree };
        else if (path.endsWith('/recent'))
            data = messages;
        else if (path.endsWith('/agent/suggest'))
            data = { suggestions: ['补充视角并续写下一章', '设计身份反转和伏笔', '检查人物行动的因果'] };
        else if (path.includes('/subscription/quota'))
            data = quota;
        else if (path.endsWith('/subscription/me'))
            data = { tier, display_name: tier === 'pro' ? '专业版' : '免费版', display_name_en: tier === 'pro' ? 'Pro' : 'Free', status: 'active' };
        else if (path.endsWith('/subscription/catalog'))
            data = catalog;
        else if (path.endsWith('/persona/onboarding'))
            data = { required: false, profile: null, recommendations: [], rollout_at: '2025-01-01T00:00:00Z', new_user_window_days: 7 };
        else if (path.includes('/persona'))
            data = { recommendations: [] };
        else if (path.endsWith('/stats/word-count-trend'))
            data = { data: [{ date: '2026-10-01', net_words: 120 }, { date: '2026-10-02', net_words: 260 }] };
        else if (path.endsWith('/stats'))
            data = stats;
        else if (path.endsWith('/public-skills/categories'))
            data = { categories: [{ name: 'Writing', count: 1 }] };
        else if (path === '/api/v1/public-skills')
            data = { skills: [publicSkill], total: 1, page: 1, page_size: 20 };
        else if (path.endsWith('/skills/my-skills'))
            data = { user_skills: [skill], added_skills: [] };
        else if (path.endsWith('/points/balance'))
            data = { available: 300, pending_expiration: 0, nearest_expiration_date: null };
        else if (path.endsWith('/points/config'))
            data = { check_in: 5, check_in_streak: 20, referral: 50, skill_contribution: 50, inspiration_contribution: 50, profile_complete: 20, pro_7days_cost: 100, streak_bonus_threshold: 7 };
        else if (path.endsWith('/points/check-in/status'))
            data = { checked_in: false, streak_days: 3, points_earned_today: 0 };
        else if (path.endsWith('/points/transactions'))
            data = { transactions: [], total: 0, page: 1, page_size: 20, total_pages: 1 };
        else if (path.endsWith('/referral/stats'))
            data = { total_invites: 10, successful_invites: 5, total_points: 500, available_points: 300 };
        else if (path.endsWith('/api-keys') || path.endsWith('/agent-api-keys'))
            // Same shape as AgentApiKeyListResponse: a bare array once made the panel call the
            // array's built-in `keys()` and crash, so the API-key tab could not be checked here.
            data = {
                keys: [{ id: 'agent-key-1', name: '写作助手（家里的电脑）', key_prefix: 'zs_ak_12ab', scopes: ['read', 'write'], is_active: true, last_used_at: '2026-10-02T08:00:00Z', expires_at: null, request_count: 12, created_at: '2026-09-01T08:00:00Z', updated_at: '2026-10-02T08:00:00Z' }],
                total: 1,
            };
        else if (path.endsWith('/resources'))
            data = { resources: [{ path: 'references/checks.md', size_bytes: 100 }] };
        else if (path.endsWith('/materials/list'))
            data = Array.from({ length: 12 }, (_, i) => ({ ...material, id: `responsive-material-${i}` }));
        else if (path.startsWith('/api/v1/materials/') && path.endsWith('/tree'))
            data = { tree: Array.from({ length: 25 }, (_, i) => ({ id: `material-chapter-${i}`, type: 'chapter', title: `第${i + 1}章 很长的标题与人物选择`, metadata: { chapter_number: i + 1 } })) };
        else if (path.includes('/materials/') && path.includes('/chapters/'))
            data = { id: 'material-chapter-0', title: '第一章 人物的选择', chapter_number: 1, word_count: 1200, summary: '章节摘要。'.repeat(30), content: '章节原文与对话。'.repeat(150) };
        else if (path.includes('/materials/') && path.endsWith('/characters'))
            data = [{ id: 'character-0', name: '顾庭轩', description: '角色简介与关系。'.repeat(30) }];
        else if (path.match(/\/materials\/responsive-material[^/]*$/))
            data = material;
        else if (path === '/api/v1/skills')
            data = { skills: [], total: 0 };
        else if (path.includes('/projects/templates'))
            data = {};
        else if (path.startsWith('/api/v1/projects/'))
            data = responsiveProjects[0];
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
    });
}
