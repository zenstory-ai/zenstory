# 法律页生产构建回归使用完整隔离工作目录

## Problem

PR258 的完整 E2E 在干净 checkout 失败：新法律页浏览器夹具只给 Vite 传 --outDir 临时路径，但现有 sitemap 插件仍向 cwd/dist 写入。CI 没有该目录，触发 ENOENT；本地旧 dist 掩盖了前提。需要保留真实生产插件与 metadata 断言，不能因测试准备失败而跳过法律页验证。

## Decision

为 worker 构建建立只含已知构建输入的临时工作目录，真实复制 Vite 配置、链接现有源文件和依赖作为构建输入，使用正常 dist 输出；sitemap 与 Vite 都在同一临时目录内工作。fixture 只提供该输出并清理自己的临时目录，不修改真实工作树输出，不复制私有环境文件。两宿主/两语言/两法律页的 SPA、刷新、canonical、OG 与 title 断言保持。

## Alternatives considered

- 在真实项目目录补建 dist：改动最小，但继续依赖共享输出并污染本地预览，未解决隔离问题。
- 关闭 sitemap 插件或删 metadata 断言：可快速消除失败，但测试不再代表真实生产构建或目标合同。
- 新增测试专用生产环境开关：易于控制，但扩大产品配置并形成另一套构建路径；临时工作目录可以复用现有配置。

## Consequences

增加少量 worker 级夹具准备，换来干净 checkout 可运行和真实插件一致性。旧失败 run 保留为证据；新提交必须重新通过 exact-source CI/E2E 后才可合并。无生产、支付或模型调用。

## Verification

- 精确代码提交 65fc00e2 的独立干净 Git worktree：初始无 apps/web/dist 与 stats.html，运行 8 个生产法律页浏览器用例全部通过（21.2s），结束后两处真实输出仍不存在。sitemap 与 visualizer 在夹具临时目录正常生成，未禁用插件。
- 两个旧公开额度用例按固定 3 条/天语义同步到新 UI 锚点；中英文 2/2 通过，项目用量、北京重置、升级控件和目录权益断言保持。
- 两份变更测试的 ESLint 与 diff-check 通过；新的 exact-source CI/E2E 将在推送后运行。
