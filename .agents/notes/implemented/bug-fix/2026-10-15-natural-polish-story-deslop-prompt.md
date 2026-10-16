# Agent Note: 选区去 AI 味提示词同步三层方法

Status: implemented

Related: [六类提示词的功能优先修订](2026-10-15-natural-polish-function-first-review.md)、[旧格式协议](2026-10-09-natural-polish-cost-noop-and-format.md)、[内容保护与句子守卫](2026-10-09-natural-polish-rewrite-not-delete.md)

## Problem

作者反馈 Web 去 AI 味只删一个逗号，却显示整段替换。主干的六类提示词已改为按叙事功能判断，不再见词必改，但还没有同步 oh-story-claudecode 当前 story-deslop 的表达、叙事用笔、故事因果三层方法。用户本次明确只更新提示词，暂不修改其余链路。

## Decision

以 oh-story-claudecode 提交 `8707158` 中的 story-deslop 及 expression、narrative-focus、story-logic、style-resolution 为依据，同步中英文静态提示词，取代六类编辑方向的组织方式；沿用主干已经按语境复核的动态检查提示。先检查故事依据，再安排用笔，最后治理电报体、短碎句及词句问题；词表和排比提示只提供定位，按语境判断是否修改。有效的心理直写、作者评论、停顿与慢场保留，不为制造差异删逗号或换同义词。

适配现有选区单轮调用：只用选区已有事实和叙述姿态，资料不足时保留未知；只恢复有依据的承接，不新增动机、事件或揭露。保留主干严格逐行原/改协议（无改动标记不能混用、不接受整段兜底）、事实与意图及否定的区分、引号与剧本结构规则。完整 skill 的外部资料读取、设计提案报告、跨行重排和大幅删段不迁入该接口。解析、检测正则、句子守卫、额度和前端均不改。

## Alternatives considered

- 直接加载完整 skill：方法可以共用，但当前接口没有工具、前文和报告通道，完整工作流不能直接执行；本次只迁移可在选区完成的判断与改写方法。
- 同时修复 diff 和纯标点结果计费：可以直接减少截图中的误导，但用户明确留到后续，本次仅改提示词。

## Consequences

收益是措辞干净的选区也会检查详略和已有因果，减少机械删逗号、换套话或把心理一律改动作。代价是提示词更长，仍受选区上下文、单轮模型和现有句子守卫限制；提示词同步不能单独证明生成质量提升。

## Verification

- 同步最新主干后，现有 natural polish service、editor natural polish API 与 round3 converge 三份测试共 61 条通过，覆盖提示词传递、严格按行解析、剧本格式、内容保护、无改动与额度行为；LLM 使用 mock，测试使用临时 SQLite。
- 后端 agent、API、services 的 ruff 检查通过；独立只读审查确认中英文方法一致，严格输出与事实保护规则保留。AST 对照确认相对主干仅两个静态提示词赋值变化。
- 本次没有进行真实模型效果测评，未修改调用逻辑、前端或部署。
