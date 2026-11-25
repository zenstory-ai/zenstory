import { expect, test, type Page } from '@playwright/test';

const adminUser = {
  id: 'admin-1',
  username: 'admin',
  email: 'admin@example.com',
  email_verified: true,
  avatar_url: null,
  is_active: true,
  is_superuser: true,
  created_at: '2026-03-01T00:00:00Z',
  updated_at: '2026-03-01T00:00:00Z',
};

const detailUser = {
  id: 'user-1',
  username: 'writer',
  email: 'writer@example.com',
  email_verified: true,
  avatar_url: null,
  is_active: true,
  is_superuser: false,
  created_at: '2026-03-01T00:00:00Z',
  updated_at: '2026-03-01T00:00:00Z',
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

async function bootstrapAdminSession(page: Page) {
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(adminUser),
    });
  });

  await page.route('**/api/auth/me/preferences', async (route) => {
    const payload = route.request().postDataJSON() as { ui_language?: string } | null;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ui_language: payload?.ui_language ?? 'zh' }),
    });
  });

  await page.route('**/api/auth/refresh', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        access_token: 'mock-access-token',
        refresh_token: 'mock-refresh-token',
        user: adminUser,
      }),
    });
  });

  await page.route('**/api/v1/projects**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

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

  await page.route('**/api/v1/materials/library-summary**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  await page.route('**/api/admin/**', async (route) => {
    const request = route.request();
    const { pathname } = new URL(request.url());

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/dashboard/growth')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(growthDashboard),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/dashboard/ux-health')) {
      const uxMetrics = {
        ttft_p50_ms: 1400,
        ttft_p95_ms: 8200,
        ttft_samples: 40,
        ttft_slow_tail_count: 2,
        ttft_slow_tail_rate: 0.05,
        duration_samples: 45,
        duration_slow_tail_count: 9,
        duration_slow_tail_rate: 0.2,
        cache_hit_tokens: 80000,
        cache_miss_tokens: 20000,
        prompt_cache_hit_rate: 0.8,
        total_rounds: 50,
        healthy_rounds: 44,
        healthy_round_rate: 0.88,
        user_stopped_rounds: 2,
        user_stopped_rate: 0.04,
        client_disconnected_rounds: 1,
        client_disconnected_rate: 0.02,
        abnormal_stop_rounds: 3,
        abnormal_stop_rate: 0.06,
        abnormal_stops: [{ reason: 'run_deadline_exceeded', count: 2, rate: 0.04 }],
        ai_edit_cards: 20,
        ai_edit_undone: 2,
        ai_edit_undo_rate: 0.1,
        ai_delete_cards: 4,
        ai_delete_restored: 1,
        ai_delete_restore_rate: 0.25,
        feedback_up: 16,
        feedback_down: 4,
        feedback_total: 20,
        thumbs_up_rate: 0.8,
        top_dislike_presets: [{ preset: 'too_slow', count: 3 }],
        ttfv_p50_minutes: 8.5,
        ttfv_samples: 12,
        w1_returning_writers: 7,
        w1_eligible_writers: 10,
        w1_retention_rate: 0.7,
        chapters_written: 18,
        net_words_written: 24500,
        mobile_users: 6,
        desktop_users: 14,
        mobile_share: 0.3,
      };
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          days: 7,
          timezone: 'Asia/Shanghai',
          current: {
            period_start: '2026-03-01T00:00:00Z',
            period_end: '2026-03-08T00:00:00Z',
            metrics: uxMetrics,
          },
          previous: {
            period_start: '2026-02-22T00:00:00Z',
            period_end: '2026-03-01T00:00:00Z',
            metrics: uxMetrics,
          },
          delta: {
            ttft_p50_ms: 0,
            ttft_p95_ms: 0,
            ttft_slow_tail_rate: 0,
            duration_slow_tail_rate: 0,
            prompt_cache_hit_rate: 0,
            healthy_round_rate: 0,
            user_stopped_rate: 0,
            client_disconnected_rate: 0,
            abnormal_stop_rate: 0,
            ai_edit_undo_rate: 0,
            ai_delete_restore_rate: 0,
            thumbs_up_rate: 0,
            ttfv_p50_minutes: 0,
            w1_retention_rate: 0,
            chapters_written: 0,
            net_words_written: 0,
            mobile_share: 0,
          },
          daily: [{
            date: '2026-03-07',
            rounds: 15,
            healthy_rate: 0.9333,
            undo_rate: 0.05,
            ttft_p95_ms: 4200,
            thumbs_up_rate: 0.85,
          }],
          definitions: {},
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/prompts')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
      return;
    }

    const usageMetrics = {
      calls: 2,
      cache_hit_tokens: 1000,
      cache_miss_tokens: 200,
      output_tokens: 50,
      cost_cny: '0.0010',
      peak_cost_cny: '0.0000',
      offpeak_cost_cny: '0.0010',
    };
    const usagePeriod = {
      timezone: 'Asia/Shanghai',
      period_start: '2026-10-07',
      period_end: '2026-10-07',
      pricing_version: 'deepseek-flash-2026-10',
      prices: {
        peak: { cache_hit: '0.04', cache_miss: '2', output: '8' },
        offpeak: { cache_hit: '0.02', cache_miss: '1', output: '4' },
      },
    };

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/usage/summary')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          window: 'today',
          ...usagePeriod,
          totals: { users: 1, ...usageMetrics },
          by_source: [{ source: 'agent', ...usageMetrics }],
          daily: [{ date: '2026-10-07', users: 1, ...usageMetrics }],
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/usage/users')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          window: 'today',
          ...usagePeriod,
          items: [{
            user_id: 'user-1',
            username: 'writer',
            email: 'writer@example.com',
            last_used_at: '2026-10-07T01:00:00Z',
            ...usageMetrics,
          }],
          total: 1,
          page: 1,
          page_size: 20,
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/usage/models')) {
      const source = new URL(request.url()).searchParams.get('source');
      const distribution = (p50: number | null, p95: number | null, samples: number) => ({ p50, p95, samples });
      const row = {
        attempts: 10, success: 4, error: 3, timeout: 1, cancelled: 1, not_recorded: 1, success_rate: 0.5,
        latency_ms: distribution(5000, 20000, 4), ttft_ms: distribution(850, 4000, 3),
        decode_tps: { value: 100, samples: 2 },
        cache_hit_tokens: 0, cache_miss_tokens: 400, output_tokens: 3000, cost_cny: '0.0120',
      };
      const routerRow = {
        ...row, attempts: 1, success: 1, error: 0, timeout: 0, cancelled: 0, not_recorded: 0, success_rate: 1,
        latency_ms: distribution(900, 900, 1), ttft_ms: distribution(null, null, 0), decode_tps: { value: null, samples: 0 },
      };
      // A feature with no calls in the window: no rows, N/A totals.
      const items = source === 'polish' ? [] : [{ model: 'deepseek-flash', ...row }, { model: 'MiniMax-M3.1-Flash', ...routerRow }];
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          window: 'today',
          ...usagePeriod,
          source,
          items,
          totals: items.length ? { ...row, attempts: 11, success: 5, success_rate: 0.5556 } : { ...row, attempts: 0, success_rate: null },
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/usage/stage-latency')) {
      const daysParam = Number(new URL(request.url()).searchParams.get('days') || '7');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          days: daysParam,
          timezone: 'Asia/Shanghai',
          period_start: '2026-10-01',
          period_end: '2026-10-07',
          slow_threshold_ms: 10000,
          total_rounds: 20,
          prepare_context_ms: { p50: 180, p95: 620, samples: 20 },
          first_output_ms: { p50: 3200, p95: 12500, samples: 18, slow_count: 3, slow_rate: 0.1667 },
          chapter_first_prose_ms: { p50: 4500, p90: 11000, p95: 14000, samples: 10, slow_count: 2, slow_rate: 0.2 },
          first_body_persisted_ms: { p50: 9000, p95: 25000, samples: 10 },
          file_body_pending_rounds: 2,
          file_body_pending_rate: 0.1,
          leaked_tool_call_rounds: 1,
          leaked_tool_call_rate: 0.05,
          user_stopped_rounds: 3,
          user_stopped_rate: 0.15,
          client_disconnected_rounds: 1,
          client_disconnected_rate: 0.05,
          desktop_vs_mobile: {
            desktop: {
              rounds: 15,
              first_output_ms: { p50: 2800, p95: 11000, samples: 14, slow_count: 2, slow_rate: 0.1429 },
              chapter_first_prose_ms: { p50: 4000, p90: 10000, p95: 13000, samples: 8, slow_count: 1, slow_rate: 0.125 },
              client_disconnected_rounds: 0,
              client_disconnected_rate: 0,
            },
            mobile: {
              rounds: 5,
              first_output_ms: { p50: 5000, p95: 14000, samples: 4, slow_count: 1, slow_rate: 0.25 },
              chapter_first_prose_ms: { p50: 6000, p90: 14000, p95: 14000, samples: 2, slow_count: 1, slow_rate: 0.5 },
              client_disconnected_rounds: 1,
              client_disconnected_rate: 0.2,
            },
            unknown: {
              rounds: 0,
              first_output_ms: { p50: null, p95: null, samples: 0, slow_count: 0, slow_rate: null },
              chapter_first_prose_ms: { p50: null, p90: null, p95: null, samples: 0, slow_count: 0, slow_rate: null },
              client_disconnected_rounds: 0,
              client_disconnected_rate: null,
            },
          },
          ai_draft_survival: {
            eligible_drafts: 8,
            survived_drafts: 6,
            co_edited_drafts: 4,
            deleted_or_reverted_drafts: 2,
            survival_rate: 0.75,
            co_edit_rate: 0.5,
            observation_hours: 24,
          },
          daily: [
            {
              date: '2026-10-07',
              rounds: 6,
              prepare_context_p50_ms: 190,
              first_output_p50_ms: 3100,
              first_output_p95_ms: 12000,
              first_output_slow_rate: 0.1667,
              chapter_first_prose_p50_ms: 4200,
              chapter_first_prose_p95_ms: 13500,
              chapter_first_prose_slow_rate: 0.25,
              file_body_pending_rate: 0.1667,
              leaked_tool_call_rate: 0,
              user_stopped_rate: 0.1667,
              client_disconnected_rate: 0,
            },
          ],
        }),
      });
      return;
    }

    if (request.method() === 'GET' && /\/api\/admin\/usage\/users\/[^/]+\/daily$/.test(pathname)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          user_id: 'user-1',
          username: 'writer',
          email: 'writer@example.com',
          days: 7,
          ...usagePeriod,
          totals: usageMetrics,
          by_source: [{ source: 'agent', ...usageMetrics }],
          daily: [{ date: '2026-10-07', ...usageMetrics }],
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/plans')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/dashboard/stats')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_users: 43,
          active_users: 31,
          new_users_today: 4,
          total_projects: 20,
          author_created_projects: 14,
          auto_initialized_projects: 6,
          total_inspirations: 5,
          pending_inspirations: 1,
          active_subscriptions: 8,
          pro_users: 8,
          total_points_in_circulation: 1200,
          today_check_ins: 10,
          active_invite_codes: 3,
          week_referrals: 2,
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/dashboard/activation-funnel')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          window_days: 7,
          period_start: '2026-03-01T00:00:00Z',
          period_end: '2026-03-08T00:00:00Z',
          steps: [],
          activation_rate: 0,
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/dashboard/upgrade-conversion')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          window_days: 7,
          period_start: '2026-03-01T00:00:00Z',
          period_end: '2026-03-08T00:00:00Z',
          total_conversions: 0,
          paid_conversions: 0,
          unattributed_conversions: 0,
          sources: [],
          channels: [],
          after_ai_quota_wall_conversions: 0,
          paid_after_ai_quota_wall_conversions: 0,
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/dashboard/upgrade-funnel')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          window_days: 7,
          period_start: '2026-03-01T00:00:00Z',
          period_end: '2026-03-08T00:00:00Z',
          totals: { expose: 0, click: 0, conversion: 0 },
          sources: [],
          surfaces: [],
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/dashboard/acquisition')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          window_days: 30,
          period_start: '2026-02-07T00:00:00Z',
          period_end: '2026-03-08T00:00:00Z',
          total_signups: 0,
          attributed_signups: 0,
          channels: [],
          sources: [],
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/audit-logs')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20 }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/users')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [detailUser], total: 1 }),
      });
      return;
    }

    if (request.method() === 'GET' && /\/api\/admin\/users\/[^/]+$/.test(pathname)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(detailUser),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/subscriptions')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20 }),
      });
      return;
    }

    if (request.method() === 'GET' && /\/api\/admin\/subscriptions\/[^/]+$/.test(pathname)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          subscription: {
            id: 'sub-1',
            user_id: detailUser.id,
            plan_id: 'plan-pro',
            status: 'active',
            current_period_start: '2026-03-01T00:00:00Z',
            current_period_end: '2099-01-01T00:00:00Z',
            cancel_at_period_end: false,
            stripe_subscription_id: null,
            stripe_customer_id: null,
            created_at: '2026-03-01T00:00:00Z',
            updated_at: '2026-03-01T00:00:00Z',
          },
          plan: {
            id: 'plan-pro',
            name: 'pro',
            display_name: '专业版',
            display_name_en: 'Pro',
            price_monthly_cents: 2900,
            price_yearly_cents: 29000,
            stripe_price_id_monthly: null,
            stripe_price_id_yearly: null,
            features: {},
            is_active: true,
            created_at: '2026-03-01T00:00:00Z',
            updated_at: '2026-03-01T00:00:00Z',
          },
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/quota/usage')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          period_start: '2026-03-01T00:00:00Z',
          period_end: '2026-04-01T00:00:00Z',
          material_decompositions: 0,
          inspiration_copies: 0,
          skills_created: 0,
        }),
      });
      return;
    }

    if (request.method() === 'GET' && /\/api\/admin\/quota\/[^/]+$/.test(pathname)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          user_id: detailUser.id,
          username: detailUser.username,
          email: detailUser.email,
          plan_name: 'pro',
          plan_display_name: '专业版',
          plan_display_name_en: 'Pro',
          ai_conversations: { used: 3, limit: -1, reset_at: null },
          material_decompositions: { used: 1, limit: 5, reset_at: '2026-04-01T00:00:00Z' },
          inspiration_copies: { used: 0, limit: -1, reset_at: null },
          custom_skills: { used: 2, limit: 20, reset_at: null },
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/points/stats')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_points_issued: 0,
          total_points_spent: 0,
          total_points_expired: 0,
          active_users_with_points: 0,
        }),
      });
      return;
    }

    if (request.method() === 'GET' && /\/api\/admin\/points\/[^/]+\/transactions$/.test(pathname)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 10 }),
      });
      return;
    }

    if (request.method() === 'GET' && /\/api\/admin\/points\/[^/]+$/.test(pathname)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          user_id: detailUser.id,
          username: detailUser.username,
          email: detailUser.email,
          available: 120,
          pending_expiration: 0,
          total_earned: 150,
          total_spent: 30,
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/payment-orders')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20, needs_attention_total: 0 }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/skills/pending')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/codes')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20 }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/inspirations')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0 }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/feedback')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0 }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/feedback/chat-votes')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          days: 7,
          timezone: 'Asia/Shanghai',
          period_start: '2026-03-01T00:00:00Z',
          period_end: '2026-03-08T00:00:00Z',
          summary: { up: 0, down: 0, total_votes: 0, up_rate: null, preset_counts: [] },
          items: [],
          total: 0,
          page: 1,
          page_size: 20,
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/check-in/stats')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          today_count: 0,
          yesterday_count: 0,
          week_total: 0,
          streak_distribution: {},
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/check-in/records')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20 }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/referrals/stats')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_codes: 0,
          active_codes: 0,
          total_referrals: 0,
          successful_referrals: 0,
          pending_rewards: 0,
          total_points_awarded: 0,
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/invites')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20 }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/referrals/rewards')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20 }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/announcements/current')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          exists: false,
          enabled: false,
          title: '',
          description: '',
          action_label: null,
          action_url: null,
          version: 0,
          updated_at: null,
        }),
      });
      return;
    }

    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: `Unmocked admin route: ${request.method()} ${pathname}` }),
    });
  });

  await page.addInitScript((user) => {
    localStorage.setItem('access_token', 'mock-access-token');
    localStorage.setItem('refresh_token', 'mock-refresh-token');
    localStorage.setItem('token_type', 'bearer');
    localStorage.setItem('user', JSON.stringify(user));
    localStorage.setItem('auth_validated_at', Date.now().toString());
  }, adminUser);
}

test('all admin routes are reachable for superuser with mocked APIs', async ({ page }) => {
  await bootstrapAdminSession(page);

  const routes = [
    '/admin',
    '/admin/ux-health',
    '/admin/acquisition',
    '/admin/users',
    '/admin/usage',
    '/admin/users/user-1',
    '/admin/prompts',
    '/admin/prompts/new',
    '/admin/skills',
    '/admin/codes',
    '/admin/subscriptions',
    '/admin/payment-orders',
    '/admin/plans',
    '/admin/audit-logs',
    '/admin/feedback',
    '/admin/announcements',
  ];

  for (const route of routes) {
    await page.goto(route);
    await expect.poll(() => new URL(page.url()).pathname).toBe(route);
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.locator('main')).toBeVisible();
  }
});

test('removed dormant and duplicate admin routes redirect cleanly', async ({ page }) => {
  await bootstrapAdminSession(page);

  const redirects: Array<[string, RegExp]> = [
    ['/admin/points', /\/admin$/],
    ['/admin/check-in', /\/admin$/],
    ['/admin/referrals', /\/admin$/],
    ['/admin/inspirations', /\/admin$/],
    ['/admin/quota', /\/admin\/users$/],
  ];

  for (const [from, target] of redirects) {
    await page.goto(from);
    await expect(page).toHaveURL(target);
    await expect(page.locator('main')).toBeVisible();
  }
});

test('admin sidebar shows 6 flat items and sub-tabs keep parent nav active', async ({ page }) => {
  await bootstrapAdminSession(page);
  await page.goto('/admin');

  const sidebarNav = page.locator('aside nav');
  if (await sidebarNav.isVisible()) {
    await expect(sidebarNav.getByRole('link')).toHaveCount(6);
    await sidebarNav.getByRole('link', { name: /^(营收|Billing)$/ }).click();
    await expect(page).toHaveURL(/\/admin\/payment-orders$/);
    await expect(sidebarNav.getByRole('link', { name: /^(营收|Billing)$/ })).toHaveAttribute('aria-current', 'page');

    const subNav = page.locator('main nav');
    await subNav.getByRole('link', { name: /^(套餐配置|Plans)$/ }).click();
    await expect(page).toHaveURL(/\/admin\/plans$/);
    await expect(sidebarNav.getByRole('link', { name: /^(营收|Billing)$/ })).toHaveAttribute('aria-current', 'page');
  }
});

test('usage page opens a user from a ?user= link', async ({ page }) => {
  await bootstrapAdminSession(page);

  await page.goto('/admin/usage?user=user-1');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('writer@example.com');
});

test('usage page shows per-model call metrics, filters by feature and explains each metric', async ({ page }) => {
  await bootstrapAdminSession(page);
  const requests: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/api/admin/usage/models')) requests.push(new URL(request.url()).search);
  });

  await page.goto('/admin/usage');
  const section = page.locator('section').filter({ has: page.getByRole('heading', { name: /^(按模型|By model)$/ }) });
  const deepseek = section.getByRole('row').filter({ hasText: 'deepseek-flash' });
  await expect(deepseek).toContainText('50%');
  await expect(deepseek).toContainText('4 / 8');
  await expect(deepseek).toContainText(/100 tokens\/(秒|s)/);
  // Not streamed: first token and decode speed are N/A, not 0.
  await expect(section.getByRole('row').filter({ hasText: 'MiniMax-M3.1-Flash' })).toContainText('N/A');
  await expect(section.getByRole('row').filter({ hasText: /全部模型|All models/ })).toContainText('55.6%');

  await section.getByRole('combobox', { name: /^(功能|Feature)$/ }).selectOption('polish');
  await expect(section).toContainText(/这段时间没有模型调用|No model calls in this period/);
  expect(requests.at(-1)).toBe('?window=today&source=polish');

  await section.getByText(/指标怎么算|How these are counted/).click();
  await expect(section).toContainText(/成功率 = 成功 ÷|Success rate = succeeded ÷/);
  await expect(section).toContainText(/解码速度 = 输出 tokens 合计|Decode speed = total output tokens/);

  const stageSection = page.locator('section').filter({
    has: page.getByRole('heading', { name: /^(阶段耗时与慢尾|Stage latency & slow tails)$/ }),
  });
  await expect(stageSection).toContainText('180 ms / 620 ms');
  await expect(stageSection).toContainText('3.2 s / 12.5 s');
  await expect(stageSection).toContainText('4.5 s / 11 s / 14 s');
  await expect(stageSection).toContainText('75%');
});

test('user detail page links to usage and lists active sections', async ({ page }) => {
  await bootstrapAdminSession(page);

  await page.goto('/admin/users/user-1');

  await expect(page.locator('main')).toBeVisible();
  await expect(page.getByRole('link', { name: /查看用量|View usage/ })).toHaveAttribute('href', '/admin/usage?user=user-1');
  for (const section of [/^(账号|Account)$/, /^(会员|Membership)$/, /^(配额|Quota)$/, /^(支付订单|Payment orders)$/]) {
    await expect(page.getByRole('heading', { name: section })).toBeVisible();
  }
  const pointsEnabled = (process.env.VITE_POINTS_ENABLED ?? 'true') === 'true';
  await expect(page.getByRole('heading', { name: /^(积分|Points)$/ })).toHaveCount(pointsEnabled ? 1 : 0);
});
