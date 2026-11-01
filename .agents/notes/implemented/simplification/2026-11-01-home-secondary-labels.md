# 首页次要标签减法

Status: implemented locally for owner review; no push, merge or deployment.

## Problem

用户认为“游戏场景原画”“README 来源”“创作工具 4”“在哪里使用 2”仍显杂乱。本次仅删并次要标签，保留上一轮结构和视觉，不改语言脚本、媒体、项目/教程或云配置。承接[首页分工](../../implemented/simplification/2026-11-01-home-single-purpose-sections.md)。

## Decision

- 删除独立原画标注行；把“游戏原画”的性质并入已有案例 caption（《西游记》游戏原画）。图本身的裁切容器直接成为固定原图链接，复用全站外侧 :focus-visible 焦点环，不在 overflow:hidden 内嵌 anchor；补无 JS 键盘聚焦与焦点环回归。原著标题仍链接固定原文，caption 仍链接案例目录，试玩入口不变。不是把原画伪装成实机；不靠隐藏文案保留来源。
- 四案例的“README 来源”简写“源码” / “Source”，保留固定 URL、原生 controls、直看/字幕链接。
- 删除工具区两个次分组标题、数字及重复讲“四套/两种”的说明。四工具和两环境仍分开排，后者显式设置 44px 顶部间距区分（当前 row-gap 为 0，不能只删除标题）；每行标题/说明已明确其用途，不把工作台当作能运行全部 skill 的产品。底部“全部 6 个项目”去掉数字。
- 删除仅服务这些行的 CSS；不加抽象或依赖。先跑当前回归，补“只有六个项目标题、不多叠一层标题”和“原画图链/已有 caption 来源性质”行为断言，不能仅对措辞写镜像测试。
- 无 JS/原生视频/直看、来源和语言兼容均为 grounded fail-safe，原样保留；播放器旧 catch 不扩展。本次无 masking fallback。

## Alternatives considered

- 只缩小字号：不减少层级，且损害阅读，拒绝。
- 删除全部出处或原画性质：元素最少，但会误导，改为已有 caption + 图片原图链接。
- 六项目混为一个连续列表：最简单，但模糊 skill 与环境；保留四加二的视觉分组，不留说明性标题和数字。

## Consequences

页面少一行图下注释、两个分组标题和一段说明；来源点击区域由文字移到图片。全部媒体、来源和项目入口保留，分组依靠间距及每行用途。验证生成器/无 JS/语言/axe/手机桌面截图、lint/typecheck/build 后停止，只更新本地预览。


## Cleanup report and verification

- 独立计划审阅先要求明确 44px 组间距及图片焦点边界，修订后 APPROVE；计划作者与审阅者分离，最终实现独立审查也为 APPROVE。随后删除独立标注、两个标题/计数、重复说明和失效 CSS，没有扩展 fallback 或新增依赖。
- 改前 33 项相关测试全绿；新增来源/工具结构回归先红 2 项、绿 27 项，实现后全套 78 项 Node 测试通过。未对“源码”等可逆措辞添加镜像测试。
- 15 项 Chromium 测试通过，包括中英语言入口、深浅色/手机桌面、无 JS 下来源/视频/知识层、原画链接 focus-visible + 3px 外环/2px offset/视口边界。中英 14 个宽度组合无横向溢出，展开态 axe 零问题。
- 964 份组织 HTML 只有两首页变化，metadata/JSON-LD 以及全部锚点目的地与数量不变；原画 SHA 不变。生成器 build 与本地预览两首页/CSS 字节相同。
- ESLint、token、i18n、TypeScript/完整 build、node --check、git diff --check 通过；仅三条原有 App hook unused-eslint-disable warnings。媒体播放逻辑与外部 CDN fail-safe 未改，不新增模型或付费调用。
- Changed files：build-org-pages 删除/合并标签；org-pages.css 删除失效样式、明确环境组间距及图片块级链接；DESIGN 更新标注与分组事实；生成器/浏览器回归锁定结构、来源和焦点边界。
- 手机/桌面工具区和首屏实际截图已检查；证据 `/tmp/zenstory-home-labels-evidence/`。Remaining risks：图片现可点击到原图；项目组别依赖间距与每项用途，不再有独立标题。语言边界不变，未部署/未进行云验证。
