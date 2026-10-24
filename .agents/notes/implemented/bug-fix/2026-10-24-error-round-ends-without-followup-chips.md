# 出错 / 超时中断的一轮不给下一步建议，超时的一轮刷新后也显示「已中断」

Status: implemented

关联：[错误时隐藏下一步卡片](2026-10-14-next-step-card-hides-on-stream-error.md)、[部分交付按保存回执说明](2026-10-14-partial-delivery-saved-receipts.md)、[回复选项排在建议最前](../feature/2026-10-20-reply-options-as-chips.md)、[可选的后续建议](../feature/2026-10-14-optional-followup-suggestions.md)、[到点先发超时终态](2026-10-14-deadline-terminal-before-cancellation-cleanup.md)。

## Problem

2026-10-24 两次合成试用：

1. 长篇首轮工具失败熔断（大纲与一张角色卡仍是空壳）后，输入框上方仍出现依赖大纲的下一步建议。
2. 长篇第二轮在高质量模式下审稿要求返修（缺场景、时间线与设定不一致），交回 writer 返修时撞上 780 秒时限；界面正确显示「这一轮写得太久，先停下了」，但建议仍是「按大纲续写第三章正文」等前进一步。留存第一章里有相邻矛盾（「六楼无电梯」后紧接「电梯门开」），说明这次返修没有完成，不能当作审定稿。

核对源码（不是只看截图）：

- ChatPanel 在 SSE error 之后照样走 onComplete（`partial: true`），只要本轮有过产出就从最后一条回复里取选项当 lead、再请求 `/suggest`；建议模型只拿到对话文字与文件标题，不知道哪些文件未确认、是否还有待返修的问题。建议缓存也会在刷新后把上一轮的 chips 带回来。
- 即时终态本身准确：时限截停发的是 `ERR_AGENT_RUN_TIMEOUT` 的 error 帧，不发 done / workflow_complete，图里也没有任何「审稿通过」事件；计费按实际产出（有正文则计费）。
- 刷新后状态丢失：普通 error 会把交付回执写进 `stop_outcome`（RoundEndNote 显示「已中断 · 已保存…」），但时限截停的 error 帧为了及时送达不附交付回执，于是这一轮从来不写 `stop_outcome`；前端也不读 `stop_reason`。刷新后这一轮看起来像正常结束的回复。
- 返修待办（交接包里的 todo）本来就不跨轮持久化；回复「继续」时由可信路由恢复 writer 与范围，模型能看到已保存的回复文字和文件，但不保证会接着完成审稿意见。

## Decision

- ChatPanel：本轮以 SSE error 结束（含时限、熔断、上游错误；以及没有终止帧就断开的 STREAM_CLOSED）时，`roundProgressRef.endedInError` 记下，onComplete 不再从回复取选项、不请求 `/suggest`，清空 chips 与这个项目的建议缓存。错误说明、重试、已保存文件和手动输入不变；作者手动点「换一批」照常请求。
- 刷新 / 重新打开：最后一条助手消息的 `stop_outcome.reason === "error"` 时同样不给 chips、不读缓存、不请求 `/suggest`。
- 服务端（`api/agent.py`）：时限截停、连接仍在、结算已完成的一轮，在残稿补存交回消息 id 之后按保存回执记 `stop_outcome(reason=error)`（既有 `_schedule_round_outcome` / RoundEndNote）。超时终态仍先发出；`stop_reason` 保留 `run_deadline_exceeded`，「继续」照常可恢复；断线后延迟结算的那条路径不变。
- 正常完成、只读完成、合法的空文件、澄清、作者停止的轮次不受影响。不新增字段、表、澄清状态机或模型调用，不按标题 / 回复文字判断是否完成。

## Alternatives considered

- **只在 error 帧带 `unwritten_files` 时不给建议。** 最强理由：范围最窄，只挡「有未确认文件」的失败。被否：时限截停的 error 帧按设计不带交付回执（后台还在补存），审稿后返修被打断时文件都已保存却仍未完成；用文件清单判断会漏掉第 2 个案例。error 终态本身就是服务端「这一轮没有正常结束」的结构化事实。
- **把交付状态传给建议模型，让它自己避开。** 最强理由：失败后仍能给出「补完大纲」这类有用建议。被否：需要新的 wire 字段与提示词改动，模型仍可能给出前进一步；失败后的下一步已有错误说明与「继续」，本批不扩展。
- **前端读 `stop_reason` 判断刷新后的超时。** 最强理由：不改服务端。被否：同一个 `run_deadline_exceeded` 也用于收尾提醒后正常结束的轮次，要再拼别的信号才能区分；复用已有的 `stop_outcome` 更直接，RoundEndNote 也随之显示「已中断」。
- **记录返修待办、下一轮自动继续返修。** 最强理由：直接回应「返修没做完」。被否：属于跨轮作品进度设计，牵涉提示、路由和质量评测，不能凭一个样稿特判。

## Consequences

- 收益：失败或超时中断的一轮不再推作者「按大纲写下一章」；刷新后超时的一轮显示「已中断」与真实保存的文件，不像正常结束。
- 代价：所有以 error 结束的轮次都不再自动给建议（包括内容已全部保存、只有收尾出错的少数情况）；作者仍可手动「换一批」。超时轮次多一次后台写 `stop_outcome`。
- 不处理：返修是否真正完成、样稿里的情节矛盾（文学质量需要另行评测，不在提示词里写样稿词语）、线上超时频率；不延长时限、不自动重试、不改思考强度。

## Verification

- `ChatPanel.mount.test.tsx` 新增两例（即时 error 轮次带回复选项与旧缓存、刷新后最后一轮 `stop_outcome.reason=error`），修改前 2 项失败（多请求一次 `/suggest`、显示旧缓存 chips），修改后 ChatPanel 相关 6 个测试文件 230 项通过。
- `test_agent_stream_hardening.py` 新增时限截停（带正文 / 不带正文、含审稿后交回 writer 的帧）记录 `stop_outcome`：修改前 2 项失败（没有 `stop_outcome`），修改后该文件与 `/agent` API 测试共 117 项通过；`stop_reason` 仍为 `run_deadline_exceeded`。
