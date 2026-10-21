# Agent Note: 英文回复按单词计「真正的回复」，时限到期只改了记忆不计费

Status: implemented

关联：[stop-output-definition-and-round-outcome](../architecture/2026-10-09-stop-output-definition-and-round-outcome.md)（本笔记只改其中「真正的回复正文」的计数口径与墙钟时限一条，其余规则不变）、[wall-clock-wrap-up](2026-10-21-wall-clock-wrap-up.md)。

## Problem

2026-10-21 英文新用户诊断（生产，一次性账号）：第一条消息「写一个约 3000 词的短篇」跑了 13.5 分钟，撞 780 秒墙钟时限。Hook designer 推理约 8 分钟，交接 Writer 后，Writer 在思考里把整篇草稿写了一遍，还没调用 `create_file` 就到时。作者只看到一句过渡旁白：「Locking the design now, then handing the manuscript to the writer so it comes back as a finished ~3,000-word story.」。文件树为空，这一轮仍扣了 1 条额度（免费版每天 3 条）。

计费判定有两处让这一轮算作「有产出」：

1. `PROSE_MIN_CHARS = 60` 按可见字符数。这个值按中文校准（60 字约两句话）；英文一句十来个词的过渡旁白就有 90 多个字母，被当成真正的回复。
2. Hook designer 调用 `update_project` 写了简介和备注（AI 提议），按现有口径属于「改了项目信息」。

## Decision

- **回复长度按 `prose_units` 计**（`agent/core/stream_billing.py`，前端 `lib/agentRoundProgress.ts` 的 `proseUnits` 同口径）。
  - 规则：拉丁字母单词每个算 `LATIN_WORD_UNITS = 1.5`，其余非空白字符（汉字、数字、标点）各算 1。阈值仍是 60，约合两句中文或 40 个英文单词。
  - 纯中文文本的结果与之前完全相同。
  - 服务端按这一段累计的原文重算（流式分片会把一个单词拆成两片），到达阈值后不再累计。
- **墙钟时限到期**只认两类产出：写进文件的正文（`file_written`，不含只改项目信息的 `update_project`），或真正的回复（`prose_output`）；另外 `ended_on_reply` 仍然计费。
  - 只改了简介、文风或备注、正文还没写出来的一轮退还，并照常补发 `quota_refunded`。
  - 作者停止、断线、失控停止、出错这几条路径的口径不变：改了项目信息仍算产出。
- **提示词**（`agent/prompts/base.py`「思考过程」）：正文不在思考里打草稿，想清结构后直接 `create_file` / `edit_file` 写进文件。
  - 理由一：思考里起草等于写两遍。
  - 理由二：时限到期时思考里的草稿存不下来，流式写进文件的部分会由取消路径保存。

## Alternatives considered

- **把 `PROSE_MIN_CHARS` 按语言分成两个阈值**。最强理由：实现最直观。没有采用：一段回复常常中英混杂（中文回复里的文件名、英文回复里的中文人名），按段落判语言再选阈值，边界比按单词折算更难说清，前后端也要多同步一个判定。
- **时限到期一律退还**。最强理由：作者没拿到完整交付，最省心。没有采用：写进文件的部分正文、已经读到的完整回答是真产出，一律退还会让超长任务反复白跑，成本靠每日成本兜底封不住。
- **时限前留足 Writer 的时间：剩余不到 N 秒就不再交接给 Writer**。最强理由：不会出现 Writer 刚开写就被截断。没有采用：本次 Writer 开始时还剩约 4 分 20 秒，足够流式写出一段开头。问题在于先在思考里写全文，提前停下只会让作者更早拿到一句「回复继续」，而且这一轮照样计费。

## Consequences

- 收益：
  - 英文作者的过渡旁白不再被当成回复。
  - 超时只留下记忆提议的一轮不占额度。
  - Writer 直接流式写文件，超时时也能保住已写部分。
- 代价：
  - 极少数纯英文的短回答（约 40 个单词以内）之后被停止，现在会退还；之前计费。
  - 时限路径多一个只看文件写入的口径，与停止 / 断线路径不再完全一致，测试分别覆盖。
  - 提示词改动的效果（Hook designer 推理时长、Writer 是否先写文件）只能在真实模型上观察。单元测试不调用付费模型，需在 staging 以外的真实使用数据里核对：看「Agent stream billing evaluated」日志里 `run_deadline_exceeded` 的占比与 `first_output_ms`。

## Verification

`cd apps/server && pytest tests/test_agent/test_stop_output_definition.py -q --no-cov`（英文旁白不算回复、分片不重复计数、时限到期只改记忆退还、写了文件计费）；`cd apps/web && pnpm exec vitest run src/lib/__tests__/agentRoundProgress.test.ts`。
