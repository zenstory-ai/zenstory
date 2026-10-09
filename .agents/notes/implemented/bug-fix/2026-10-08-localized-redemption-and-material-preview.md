# Agent Note: 邀请码校验限频返回错误码，素材预览按请求语言输出并翻译枚举

## Problem

- 兑换码失败与限频的英文文案已由 `2026-10-08-billing-copy-glossary.md` 改为 `ERR_REDEMPTION_*` 错误码，本批不再重复。剩下的同类问题：公开接口 `POST /referral/codes/{code}/validate` 限频时返回英文句子「Rate limit exceeded. Please try again later.」，前端无法翻译。
- 素材预览 `GET /materials/{novel_id}/{entity_type}/{entity_id}/preview`（`MaterialPreview.tsx` 渲染，导入素材时也直接用作文件内容）的 markdown 只有中文标题，且把抽取得到的英文枚举原样输出，例如「**类型**: special_physique」「## 剧情类型 main」「**关系**: master_disciple」。英文界面看到中文标题，中文界面看到英文键。

## Decision

- `api/referral.py` 限频改为 `APIException(ErrorCode.AUTH_RATE_LIMIT_EXCEEDED, 429)`，状态码不变，`detail`/`error_code` 都是该码；前端 `errors.json` 已有中英文。
- `api/material_utils.py` 的六个 `format_*_to_markdown` 增加 `lang` 参数（默认 `"zh"`，因此 agent 上下文组装器调用方式不变）。标题、「无/未知」、列表分隔符和来源行按语言输出。
- 枚举值映射为标签：剧情类型、关系类型、金手指类型与前端 `materials.json` 的 `enums` 块逐字一致；另外补了抽取流程实际写入的角色定位（protagonist/antagonist/supporting/minor）和关系态度（positive/negative/neutral/complex）。未知的英文值显示为「其他/Other」，中文自由文本原样保留。
- 预览接口已有的 `_resolve_lang(Accept-Language)` 结果传给格式化函数；导入接口复用预览，所以导入生成的文件内容也随界面语言。

## Alternatives considered

- **前端解析 markdown 后再翻译枚举**。最强理由：后端不必维护一份标签。被否：预览内容同时用作导入文件的正文，前端只翻译显示会让导入的文件仍含原始英文键；且解析 markdown 文本比在生成处映射脆弱得多。
- **只翻译枚举、标题保持中文**。最强理由：改动更小，agent 上下文完全不变。被否：英文用户仍会看到整篇中文标题，问题只修了一半。
- **未知英文值原样输出**。最强理由：不丢信息。被否：这正是用户看到原始键的来源；未知值多为模型偏离约定的输出，归入「其他」与前端徽标的回退一致。

## Consequences

- 好处：中英文界面的预览与导入文件都不再出现原始枚举键或另一种语言的标题；邀请码校验限频可被前端本地化。
- 代价：枚举标签在后端与 `materials.json` 各有一份，新增枚举时需要两处同步（代码注释已标明）。剧情点（plot）类型目前没有预览格式化函数，未纳入。
- agent 上下文中的素材 markdown 现在显示中文标签而非英文键（默认 `zh`），对模型理解无负面影响。
