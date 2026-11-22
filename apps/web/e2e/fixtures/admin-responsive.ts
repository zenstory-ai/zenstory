import type { Page, Route } from '@playwright/test';

export const adminUser = {
  id: 'admin-1', username: 'admin', email: 'admin@example.com', email_verified: true,
  is_active: true, is_superuser: true, created_at: '2026-03-01T00:00:00Z', updated_at: '2026-03-01T00:00:00Z',
};

export const stamp = '2026-03-01T00:00:00Z';
export const longText = 'A deliberately long populated value used to verify that dense admin content wraps or scrolls locally without widening the document.';
export const users = Array.from({ length: 3 }, (_, index) => ({
  id: `user-${index + 1}`,
  username: `writer-with-a-long-name-${index + 1}`,
  email: `long-admin-responsive-address-${index + 1}@example.com`,
  email_verified: true,
  is_active: true,
  is_superuser: index === 0,
  created_at: stamp,
  updated_at: stamp,
}));

export const prompt = {
  id: 'prompt-1', project_type: 'novel', role_definition: longText, capabilities: longText,
  directory_structure: '/novel/chapters/chapter-001.md', content_structure: longText,
  file_types: '.md,.txt', writing_guidelines: longText, include_dialogue_guidelines: true,
  primary_content_type: 'novel', is_active: true, version: 7, created_at: stamp, updated_at: stamp,
};

const growthMetrics = {
  new_users: 43,
  ai_active_users: 31,
  cohort_activated_users: 19,
  cohort_activation_rate: 0.4419,
  paid_orders: 9,
  revenue_cents: 123456,
  paid_users: 8,
  cohort_paid_users: 5,
  signup_to_paid_rate: 0.1163,
  grant_upgrade_events: 4,
  grant_upgrade_users: 3,
  grant_channels: [{ channel: 'admin_update', events: 4, users: 3 }],
};

const growthDashboard = {
  days: 7,
  timezone: 'Asia/Shanghai',
  current: {
    period_start: '2026-03-01T00:00:00Z',
    period_end: '2026-03-08T00:00:00Z',
    metrics: growthMetrics,
  },
  previous: {
    period_start: '2026-02-22T00:00:00Z',
    period_end: '2026-03-01T00:00:00Z',
    metrics: {
      ...growthMetrics,
      new_users: 35,
      ai_active_users: 24,
      paid_orders: 6,
      revenue_cents: 84500,
      paid_users: 6,
    },
  },
  daily: [{
    date: '2026-03-07',
    ...growthMetrics,
    new_users: 17,
    ai_active_users: 13,
    paid_orders: 4,
    revenue_cents: 32109,
  }],
  definitions: {
    window: 'Beijing-time half-open reporting window ending at the request cutoff.',
    activation: 'Signup cohort with a live model call observed by the period cutoff.',
    paid: 'Confirmed paid orders; pending, refunded, and granted upgrades are excluded.',
    grants: 'Non-payment subscription upgrades grouped separately from revenue.',
  },
};

const json = (route: Route, body: unknown) => route.fulfill({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(body),
});

const usageMetrics = {
  calls: 1289, cache_hit_tokens: 1234567, cache_miss_tokens: 23456, output_tokens: 34567,
  cost_cny: '1234.56', peak_cost_cny: '123.45', offpeak_cost_cny: '1111.11',
};
const usagePeriod = {
  timezone: 'Asia/Shanghai', period_start: '2026-03-01', period_end: '2026-03-07',
  pricing_version: 'test-pricing',
  prices: { peak: { cache_hit: '0.04', cache_miss: '2', output: '8' }, offpeak: { cache_hit: '0.02', cache_miss: '1', output: '4' } },
};
export const paymentOrder = {
  id: 'order-1', user_id: users[0].id, username: users[0].username, email: users[0].email,
  out_trade_no: 'ZENSTORYORDER20260301VERYLONGNUMBER1234567890', trade_no: 'PROVIDER202603012345678901234567890',
  plan_name: 'pro', plan_display_name: 'Professional Annual Writer', cycle: 'year', amount_cents: 39900,
  payment_method: 'alipay', status: 'paid', fulfillment_status: 'failed', created_at: stamp,
  paid_at: stamp, fulfilled_at: null, failure_reason: longText, upgrade_source: 'editor_upgrade_banner',
};

export async function bootstrapAdminSession(page: Page) {
  await page.addInitScript((user) => {
    localStorage.setItem('access_token', 'mock-access-token');
    localStorage.setItem('refresh_token', 'mock-refresh-token');
    localStorage.setItem('token_type', 'bearer');
    localStorage.setItem('user', JSON.stringify(user));
    localStorage.setItem('auth_validated_at', Date.now().toString());
  }, adminUser);

  await page.route('**/api/auth/me', (route) => json(route, adminUser));
  await page.route('**/api/auth/refresh', (route) => json(route, { access_token: 'mock-access-token', refresh_token: 'mock-refresh-token', user: adminUser }));
  await page.route('**/api/v1/projects**', (route) => json(route, []));
  // The materials library checks the plan before requesting the summary.
  await page.route('**/api/v1/subscription/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tier: 'pro',
        status: 'active',
        display_name: 'Pro',
        display_name_en: 'Pro',
        current_period_end: null,
        days_remaining: null,
        features: { materials_library_access: true },
      }),
    });
  });

  await page.route('**/api/v1/materials/library-summary**', (route) => json(route, []));
  await page.route('**/api/admin/**', async (route) => {
    const { pathname } = new URL(route.request().url());
    const method = route.request().method();
    if (method !== 'GET') return json(route, { success: true, message: 'ok' });

    if (pathname === '/api/admin/users/user-1') return json(route, users[0]);
    if (pathname === '/api/admin/subscriptions/user-1') return json(route, {
      subscription: { id: 'sub-1', user_id: 'user-1', plan_id: 'plan-pro', status: 'active', current_period_start: stamp, current_period_end: '2027-03-01T00:00:00Z', created_at: stamp, updated_at: stamp },
      plan: { id: 'plan-pro', name: 'pro', display_name: 'Professional Annual Writer', display_name_en: 'Professional Annual Writer', features: {}, is_active: true }, quota: null,
    });
    if (pathname === '/api/admin/points/user-1') return json(route, { user_id: 'user-1', username: users[0].username, email: users[0].email, available: 9000, pending_expiration: 1000, total_earned: 12000, total_spent: 3000 });
    if (pathname === '/api/admin/points/user-1/transactions') return json(route, { items: [{ id: 'tx-detail', user_id: 'user-1', amount: 1000, balance_after: 9000, transaction_type: 'admin_adjust', description: longText, created_at: stamp }], total: 1 });
    if (pathname === '/api/admin/quota/user-1') return json(route, { user_id: 'user-1', username: users[0].username, email: users[0].email, plan_name: 'pro', plan_display_name: 'Professional Annual Writer', plan_display_name_en: 'Professional Annual Writer', ai_conversations: { used: 4500, limit: -1, reset_at: null }, material_decompositions: { used: 2, limit: 5, reset_at: null }, custom_skills: { used: 8, limit: 20, reset_at: null }, inspiration_copies: { used: 4, limit: 500, reset_at: null } });
    if (pathname === '/api/admin/payment-orders') return json(route, { items: [paymentOrder], total: 1, page: 1, page_size: 20, needs_attention_total: 1 });
    if (pathname === '/api/admin/usage/summary') return json(route, { window: '7d', ...usagePeriod, totals: { users: 1234, ...usageMetrics }, by_source: [{ source: 'agent', ...usageMetrics }], daily: [{ date: '2026-03-01', users: 1234, ...usageMetrics }] });
    if (pathname === '/api/admin/usage/users') return json(route, { window: '7d', ...usagePeriod, items: [{ user_id: 'user-1', username: users[0].username, email: users[0].email, last_used_at: stamp, ...usageMetrics }], total: 1, page: 1, page_size: 20 });
    if (pathname === '/api/admin/usage/users/user-1/daily') return json(route, { user_id: 'user-1', username: users[0].username, email: users[0].email, days: 7, ...usagePeriod, totals: usageMetrics, by_source: [{ source: 'agent', ...usageMetrics }], daily: [{ date: '2026-03-01', ...usageMetrics }] });

    if (pathname.endsWith('/dashboard/stats')) return json(route, { total_users: 1289, active_users: 934, new_users_today: 37, total_projects: 4821, author_created_projects: 4500, auto_initialized_projects: 321, total_inspirations: 271, pending_inspirations: 14, active_subscriptions: 408, pro_users: 379, total_points_in_circulation: 902341, today_check_ins: 611, active_invite_codes: 88, week_referrals: 143 });
    if (pathname.endsWith('/dashboard/growth')) return json(route, growthDashboard);
    if (pathname.endsWith('/dashboard/ux-health')) {
      const uxMetrics = {
        ttft_p50_ms: 1400, ttft_p95_ms: 8200, ttft_samples: 40, ttft_slow_tail_count: 2, ttft_slow_tail_rate: 0.05,
        duration_samples: 45, duration_slow_tail_count: 9, duration_slow_tail_rate: 0.2,
        cache_hit_tokens: 80000, cache_miss_tokens: 20000, prompt_cache_hit_rate: 0.8,
        total_rounds: 50, healthy_rounds: 44, healthy_round_rate: 0.88,
        user_stopped_rounds: 2, user_stopped_rate: 0.04, client_disconnected_rounds: 1, client_disconnected_rate: 0.02,
        abnormal_stop_rounds: 3, abnormal_stop_rate: 0.06,
        abnormal_stops: [{ reason: 'run_deadline_exceeded', count: 2, rate: 0.04 }],
        ai_edit_cards: 20, ai_edit_undone: 2, ai_edit_undo_rate: 0.1,
        ai_delete_cards: 4, ai_delete_restored: 1, ai_delete_restore_rate: 0.25,
        feedback_up: 16, feedback_down: 4, feedback_total: 20, thumbs_up_rate: 0.8,
        top_dislike_presets: [{ preset: 'too_slow', count: 3 }],
        ttfv_p50_minutes: 8.5, ttfv_samples: 12,
        w1_returning_writers: 7, w1_eligible_writers: 10, w1_retention_rate: 0.7,
        chapters_written: 18, net_words_written: 24500,
        mobile_users: 6, desktop_users: 14, mobile_share: 0.3,
      };
      return json(route, {
        days: 7, timezone: 'Asia/Shanghai',
        current: { period_start: '2026-03-01T00:00:00Z', period_end: '2026-03-08T00:00:00Z', metrics: uxMetrics },
        previous: { period_start: '2026-02-22T00:00:00Z', period_end: '2026-03-01T00:00:00Z', metrics: uxMetrics },
        delta: {
          ttft_p50_ms: -200, ttft_p95_ms: -800, ttft_slow_tail_rate: -0.05, duration_slow_tail_rate: -0.1,
          prompt_cache_hit_rate: 0.1, healthy_round_rate: 0.03, user_stopped_rate: -0.01, client_disconnected_rate: -0.005,
          abnormal_stop_rate: -0.015, ai_edit_undo_rate: -0.1, ai_delete_restore_rate: 0.25, thumbs_up_rate: 0.1,
          ttfv_p50_minutes: -1.5, w1_retention_rate: 0.2, chapters_written: 6, net_words_written: 6500, mobile_share: 0.1,
        },
        daily: [{ date: '2026-03-07', rounds: 15, healthy_rate: 0.9333, undo_rate: 0.05, ttft_p95_ms: 4200, thumbs_up_rate: 0.85 }],
        definitions: {},
      });
    }
    if (pathname.endsWith('/feedback/chat-votes')) return json(route, {
      days: 7, timezone: 'Asia/Shanghai', period_start: '2026-03-01T00:00:00Z', period_end: '2026-03-08T00:00:00Z',
      summary: { up: 16, down: 4, total_votes: 20, up_rate: 0.8, preset_counts: [{ preset: 'too_slow', count: 3 }] },
      items: [{ message_id: 'msg-1', user_id: users[0].id, username: users[0].username, vote: 'down', preset: 'too_slow', comment: longText, created_at: stamp }],
      total: 1, page: 1, page_size: 20,
    });
    if (pathname.endsWith('/dashboard/acquisition')) return json(route, { window_days: 30, period_start: '2026-02-07T00:00:00Z', period_end: '2026-03-08T00:00:00Z', total_signups: 120, attributed_signups: 95, channels: [{ channel: 'search', signups: 60, activated_users: 30, activation_rate: 0.5, paid_users: 6, paid_rate: 0.1 }], sources: [{ channel: 'search', source: 'www.google.com', signups: 60, activated_users: 30, activation_rate: 0.5, paid_users: 6, paid_rate: 0.1 }] });
    if (pathname.endsWith('/announcements/current')) return json(route, { exists: true, enabled: true, title: 'Maintenance notice', description: longText, action_label: 'Docs', action_url: '/docs', version: 2, updated_at: stamp });
    if (pathname.endsWith('/dashboard/activation-funnel')) return json(route, { window_days: 7, period_start: stamp, period_end: stamp, activation_rate: 0.378, steps: [{ event_name: 'signup', label: 'Registered account with long attribution label', users: 1200, conversion_from_previous: null, drop_off_from_previous: null }, { event_name: 'created', label: 'Created first writing project', users: 454, conversion_from_previous: 0.378, drop_off_from_previous: 0.622 }] });
    if (pathname.endsWith('/dashboard/upgrade-conversion')) return json(route, { window_days: 7, period_start: stamp, period_end: stamp, total_conversions: 73, paid_conversions: 69, channels: [{ channel: 'zpay', conversions: 69, paid: true }], unattributed_conversions: 4, after_ai_quota_wall_conversions: 12, paid_after_ai_quota_wall_conversions: 10, sources: [{ source: 'editor-upgrade-banner-with-long-campaign-name', conversions: 69, share: 94.5 }] });
    if (pathname.endsWith('/dashboard/upgrade-funnel')) return json(route, { window_days: 7, period_start: stamp, period_end: stamp, totals: { expose: 1800, click: 352, conversion: 73 }, sources: [{ source: 'editor-upgrade-banner-with-long-campaign-name', exposes: 1800, clicks: 352, conversions: 73, click_through_rate: 19.6, conversion_rate_from_click: 20.7, conversion_rate_from_expose: 4.1 }] });
    if (pathname === '/api/admin/users') return json(route, { items: users, total: users.length });
    if (pathname === '/api/admin/prompts') return json(route, [prompt]);
    if (pathname.endsWith('/api/admin/prompts/novel')) return json(route, prompt);
    if (pathname.endsWith('/api/admin/skills/pending')) return json(route, [{ id: 'skill-1', name: 'Long-form narrative continuity reviewer', description: longText, instructions: longText, category: 'writing-and-development', author_id: 'user-1', author_name: 'writer-with-a-long-name', status: 'pending', reviewed_by: null, reviewer_name: null, reviewed_at: null, rejection_reason: null, created_at: stamp }]);
    if (pathname === '/api/admin/codes') return json(route, { items: [{ id: 'code-1', code: 'RESPONSIVE-AUDIT-LONG-CODE-2026', tier: 'pro', duration_days: 365, code_type: 'multi_use', max_uses: 500, current_uses: 214, is_active: true, notes: longText, created_at: stamp, updated_at: stamp }], total: 1, page: 1, page_size: 20 });
    if (pathname === '/api/admin/subscriptions') return json(route, { items: [{ id: 'sub-1', user_id: 'user-1', username: users[0].username, email: users[0].email, plan_name: 'pro', plan_display_name: 'Professional Annual Writer', status: 'active', current_period_start: stamp, current_period_end: '2027-03-01T00:00:00Z', created_at: stamp, updated_at: stamp, has_subscription_record: true }], total: 1, page: 1, page_size: 20 });
    if (pathname === '/api/admin/plans') return json(route, [{ id: 'plan-pro', name: 'pro', display_name: 'Professional Writer Plan', display_name_en: 'Professional Writer Plan', price_monthly_cents: 1999, price_yearly_cents: 19999, features: { ai_conversations: 5000, material_uploads: 250, skill_creates: 100 }, is_active: true, created_at: stamp, updated_at: stamp }]);
    if (pathname === '/api/admin/audit-logs') return json(route, { items: [{ id: 'log-1', admin_id: 'admin-1', admin_name: 'super-administrator-with-long-name', action: 'subscription.configuration.updated', resource_type: 'subscription_plan_configuration', resource_id: 'professional-annual-plan-identifier', details: longText, old_value: { description: longText }, new_value: { description: `${longText} Updated.` }, ip_address: '2001:db8:3333:4444:5555:6666:7777:8888', user_agent: longText, created_at: stamp }], total: 1, page: 1, page_size: 20 });
    if (pathname === '/api/admin/inspirations') return json(route, { items: [{ id: 'ins-1', name: 'Urban fantasy mystery with a deliberately long title', description: longText, tags: ['urban-fantasy', 'slow-burn-mystery', 'found-family'], source: 'community', status: 'pending', is_featured: false, sort_order: 1, copy_count: 234, creator_id: 'user-1', creator_name: users[0].username, reviewer_id: null, created_at: stamp, updated_at: stamp }], total: 1 });
    if (pathname === '/api/admin/feedback') return json(route, { items: [{ id: 'feedback-1', user_id: 'user-1', username: users[0].username, email: users[0].email, source_page: 'editor', source_route: '/projects/extremely-long-project-identifier/editor', issue_text: longText, trace_id: 'trace-extremely-long-correlation-identifier-1234567890', request_id: 'request-1234567890', agent_run_id: 'agent-run-1234567890', project_id: 'project-1234567890', agent_session_id: 'session-1234567890', has_screenshot: false, screenshot_original_name: null, screenshot_content_type: null, screenshot_size_bytes: null, screenshot_download_url: null, status: 'open', created_at: stamp, updated_at: stamp }], total: 1 });
    if (pathname.endsWith('/points/stats')) return json(route, { total_points_issued: 9999999, total_points_spent: 7654321, total_points_expired: 123456, active_users_with_points: 8765 });
    if (pathname.endsWith('/points/transactions')) return json(route, { items: [{ id: 'tx-1', user_id: 'user-1', username: users[0].username, amount: 1000, balance_after: 9000, transaction_type: 'admin_adjustment_with_long_label', source_id: null, description: longText, expires_at: null, is_expired: false, created_at: stamp }], total: 1, page: 1, page_size: 20 });
    if (pathname.includes('/points/users/')) return json(route, { user_id: 'user-1', username: users[0].username, email: users[0].email, available: 9000, pending_expiration: 1000, total_earned: 12000, total_spent: 3000 });
    if (pathname.endsWith('/check-in/stats')) return json(route, { today_count: 611, yesterday_count: 598, week_total: 4012, streak_distribution: { 1: 120, 7: 89, 30: 17 } });
    if (pathname.endsWith('/check-in/records')) return json(route, { items: [{ id: 'check-1', user_id: 'user-1', username: users[0].username, check_in_date: '2026-03-01', streak_days: 30, points_earned: 100, created_at: stamp }], total: 1, page: 1, page_size: 20 });
    if (pathname.endsWith('/referrals/stats')) return json(route, { total_codes: 88, active_codes: 74, total_referrals: 1430, successful_referrals: 1190, pending_rewards: 19, total_points_awarded: 89200 });
    if (pathname.endsWith('/api/admin/invites')) return json(route, { items: [{ id: 'invite-1', code: 'INVITE-RESPONSIVE-AUDIT-2026', owner_id: 'user-1', owner_name: users[0].username, current_uses: 52, max_uses: 100, is_active: true, created_at: stamp, expires_at: '2027-03-01T00:00:00Z' }], total: 1, page: 1, page_size: 20 });
    if (pathname.endsWith('/referrals/rewards')) return json(route, { items: [{ id: 'reward-1', user_id: 'user-1', username: users[0].username, reward_type: 'successful_referral_award', amount: 500, source: 'invite-responsive-audit-2026', is_used: false, expires_at: '2027-03-01T00:00:00Z', created_at: stamp }], total: 1, page: 1, page_size: 20 });
    if (pathname.endsWith('/quota/usage')) return json(route, { period_start: '2026-03-01T00:00:00+00:00', period_end: '2026-04-01T00:00:00+00:00', material_decompositions: 48321, skills_created: 9432, inspiration_copies: 29110 });
    if (pathname.includes('/quota/users/')) return json(route, { user_id: 'user-1', username: users[0].username, email: users[0].email, plan_name: 'pro', plan_display_name: 'Professional Annual Writer', plan_display_name_en: 'Professional Annual Writer', ai_conversations: { used: 4500, limit: 5000, reset_at: null }, material_decompositions: { used: 220, limit: 250, reset_at: null }, custom_skills: { used: 88, limit: 100, reset_at: null }, inspiration_copies: { used: 410, limit: 500, reset_at: null } });
    return json(route, {});
  });
}
