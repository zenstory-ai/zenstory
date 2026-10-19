# 新账号首日隐藏 2/3 时的升级按钮

Status: rejected — 首轮问题已经在第一批里解决；这个按钮只是被动显示的标签，不会弹窗；在没有数据的情况下改转化入口，与 10-17 的订阅转化决定冲突。等首日漏斗有了数据再重新评估。

## Problem

2026-10-19 新用户走查中，人物 A（网文新手）的路径是：

1. 第 1 条消息被 AI 追问；
2. 第 2 条发完，额度标签变成「开通 Pro，AI 消息不限条数」（`upgradeTriggerStrategy` 在用量到一半时进入 `reminder_50`，`QuotaBadge` 随之显示升级按钮）；
3. 第 3 条才拿到框架。

增长 PM 的原方案把这一条列为开放问题：新账号第一天，是否隐藏这个升级按钮，把额度改成显示「还能发 N 条」。

## Proposal

新账号注册后的第一个北京自然日内，`QuotaBadge` 在 `reminder_50` 阶段不显示升级按钮，只显示「今天还能发 N 条」；额度用完时照常出现额度卡和升级提示。

## Alternatives considered

- **按提案执行**
  - 最强论据：作者刚看到 AI 写出的第一段正文，就被推销付费，可能削弱信任和留存。
  - 不选的原因：
    1. 走查里让人难受的，主要是「第一条就被追问」。第一批已改为首轮先写正文（`implemented/feature/2026-10-19-first-turn-writes-before-asking.md`），作者在看到升级按钮之前已经拿到正文。
    2. 这个按钮只是额度标签里的一段文字，不弹窗，也不拦截操作。
    3. `implemented/feature/2026-10-17-subscription-conversion-focus.md` 刚把订阅页的价值主张和转化路径收窄，没有任何数据支持现在削减首日的转化入口。
    4. 起手卡已经在聊天空态写明「今天还能发 N 条」，额度信息在发第一条之前就看得到。
- **维持现状，用数据决定**：采用。第一批上线后，新的首日漏斗（`first_ai_message_sent` / `first_ai_reply_succeeded` / `first_ai_action_accepted`）与现有的 `upgrade_funnel` 埋点（按阶段区分来源，例如 `chat_quota:reminder_50`）可以对照着看：首日在 `reminder_50` 点了升级的人，第二天的回访率是否明显偏低。有了这份数据再决定。

## Consequences

- 新账号第一天，用到 2/3 时仍然会看到升级按钮。
- 重新评估需要一份新的记录，写明依据的数据。
