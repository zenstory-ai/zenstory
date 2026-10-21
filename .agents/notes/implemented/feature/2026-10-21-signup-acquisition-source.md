# Agent Note: 注册账号记录首次来源，管理后台按渠道查看

Status: implemented

## Problem

产品不知道新账号从哪里来。前端只把 `?source=`（官网链接带的内部来源码）放进当前标签页的 sessionStorage，随 PostHog 注册事件发送；不看 UTM、不看外部来源站点，服务端不保存，管理后台也没有任何来源视图，无法判断搜索、AI 助手、社区或官网各自带来多少注册、其中多少人真正用了 AI 或付费。

## Decision

- 前端 `lib/acquisition.ts` 在启动时记录首次触达：内部 `source`、`utm_source` / `utm_medium` / `utm_campaign`、外部来源站点的主机名、落地路径（ID 段替换为 `:id`），存 localStorage 30 天。第一次带来源信号的访问为准；之前只有直接访问时，后来带信号的访问覆盖它。OAuth 回调与支付返回页、本站与 API 主机、Google 账号页不算来源。
- 邮箱注册在请求体 `acquisition` 中发送；Google 登录通过 `/api/auth/google?acq=` 放进 OAuth state，新账号创建时读取。
- 后端 `services/usage/acquisition_service.py` 只保留合法的代码、主机名和路径，推导渠道：付费广告（utm_medium 为 cpc 等）→ AI 助手 / 搜索引擎 / 社区与社交 / ZenStory 官网（按来源主机）→ 官网内部来源码 → 推广活动（有 utm_source）→ 其他网站 → 直接访问。结果写进已有 `signup_success` 激活事件的 `event_metadata.acquisition`，不改表结构、不需要迁移。
- 管理后台新增「增长 › 注册来源」（`/admin/acquisition`，`GET /api/admin/dashboard/acquisition?days=7|14|30|90`）：北京时间窗口内注册的非管理员账号，按渠道和按来源（UTM 来源、来源主机或内部来源码，前 30 条）统计注册数、用过 AI（作者主动的 AI 用量，与增长看板同口径）和付费（已支付订单）人数及比例。没有来源记录的账号归为「未记录」。

## Alternatives considered

- **新建 user_acquisition 表或在 user 表加列。** 最强理由：查询直接、语义独立。被否：生产不在部署时跑迁移，新增列需要单独的备份与迁移窗口；注册事件已有 JSON 元数据，且每个账号只有一条。
- **只依赖 PostHog 归因。** 最强理由：PostHog 自带 UTM 与来源分析。被否：分析数据可被拦截或关闭，且无法与服务端的激活、付费口径在同一个后台视图里对齐。
- **末次触达归因。** 最强理由：更接近促成注册的那次访问。未采用：对早期增长更关心「最初从哪里知道」；直接访问不覆盖已有来源，避免回访把来源冲掉。

## Consequences

- 收益：后台能看到各渠道的注册、激活与付费；数据只含来源代码与主机名，不含完整 URL 或个人信息。
- 代价：官网（zenstory.ai）是独立站点，访客从搜索进入官网再点到 App 时，App 只看到官网与链接的来源码，原始搜索来源丢失；禁用本地存储或换设备注册的账号记为「未记录」；上线前注册的账号全部是「未记录」。后台只统计注册，不统计访问量。
