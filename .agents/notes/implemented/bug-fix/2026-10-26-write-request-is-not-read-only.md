# Agent Note: 路由同时判「只读」和「要写正文」时不按只读执行

Status: implemented

关联：[交接以用户本轮范围为准](2026-10-03-agent-handoff-respects-user-scope.md)（read_only / write_content 的定义）、[语义路由](2026-10-24-agent-semantic-routing-and-evidence.md)。

## Problem

生产 10-26 10:19（用户 7f8faa32）：作者在上一轮之后说「一并扩到2000字先」，路由给出 `read_only=true`、`write_content=true`、scope「第一章保持现有情节扩至2000字以上」。`read_only=true` 让写文件的工具一律拒绝、计划交接清空，writer 只能回复「这一轮没有文件写入权限」，把整章扩写稿贴进对话。作者要再发一次才能存进文件，多花一次额度。

两个字段是对同一个请求的判断：`write_content=true` 是「本轮要产出或改写正文」，`read_only=true` 是「明确不改文件」。要写正文就要改文件，两者同时为真时自相矛盾，原来的代码按更严格的只读执行。

## Decision

`RouterDecision` 校验时，`read_only=true` 且 `write_content=true` 改为 `read_only=false`，并记 WARNING `Router marked a write request read-only; not treating it as read-only`。`write_content`、`scope`、`requested_file_types` 等其余判断不变。所有消费方（图的只读拦截、写工具拒绝、计划交接裁剪、系统指令里的范围约束）都读 `routing_metadata`，由同一个决策对象生成，因此一致生效；接续上一轮（resume / 补问作答）合成的决策同样经过这次校验。

回归：`tests/test_agent/test_router_compatibility.py` 重放生产那次路由输出（quick / standard 两种），确认落库路由不是只读、`write_content` 保持为真、standard 仍计划交给 writer。原 `test_read_only_clears_every_planned_agent` 用的正是这对矛盾组合，改为只设 `read_only=true`，它要验证的「只读清空计划交接」不变。

## Alternatives considered

- **矛盾时按只读执行**（原行为）。最强理由：最保守，绝不会在作者说「别改」时改文件。被否：真正的「别改」请求不会同时被判成要写正文；这次的代价是作者明确的写请求被吞掉，而且会再发生。
- **矛盾时让路由重判或请作者确认**。最强理由：不替模型猜。被否：多一次路由调用或一张确认卡，都让作者多等一轮；而「要写正文」这一半与作者原话一致。
- **把 `write_content=true` 改成 false 以服从只读**。最强理由：保住只读的保护。被否：等同原行为，作者的写请求仍然丢失。

## Consequences

- 收益：路由给出矛盾判断时，作者要写的内容能写进文件，不再只贴在对话里。
- 代价：若作者原话确实是「先别改文件」而路由误判成要写正文，现在会改文件；改动可在历史版本里找回。
- 不处理：路由判成纯只读（`read_only=true`、`write_content=false`）但作者其实要写的情况（10:13「希望每一章都能拓展到2000字」）是路由的语义判断问题，不在本次范围。
