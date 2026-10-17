# 法律页按宿主保留对应阅读器

## Problem

App 已有 React 隐私政策与服务条款，但 sitePrefixes 同时触发客户端和 Vercel App→组织站跳转；构建根目录的静态法律文件还会先于 SPA rewrite 命中。用户要求工作台入口回到原有工作台版本。

## Decision

将 privacy-policy、terms-of-service 移到现有 sharedPrefixes，与 docs 共用双宿主机制。组织站重写到 _site 下静态双语页，App 原 URL 交给现有 React 路由。泛化 _site 内部路径别名，避免实现路径暴露；正式/预览宿主规则一致。canonical、组织站 sitemap、相对链接与两套现有 JSON 正文均不改。

本决定局部反转[法律页首屏决定](../../implemented/bug-fix/2026-10-13-legal-first-paint-and-app-home.md)中 App 也使用组织站静态阅读器的取舍；保留组织站无 JS、稳定首屏与双语全文。

## Alternatives considered

- 新增 /legal 路由：路径归属清晰，但要求所有入口迁移和旧 URL 兼容，不能恢复用户要求的原路径。
- 逐条删除 App redirect：改动表面较少，但物理静态文件仍遮蔽 SPA，还需复制 shared-prefix 搬移机制。
- 沿用组织版：保留首屏无需 JS，但与用户指定工作台阅读器不符。

## Consequences

App 法律页恢复对 JavaScript 的依赖，组织站继续无 JS 阅读。法律正文与已有 canonical 不变；既有条款内容差异不在本次改写授权内。构建测试覆盖静态文件位置、全文、转义和别名；域名矩阵覆盖 App 不跳走、组织站仍静态与预览 noindex。开发和验证不构成线上部署。

验证使用生产 Vite bundle 在 app/app-preview 页面 URL 下离线提供本地字节，覆盖两语言、两法律页的页脚 SPA 导航与刷新，canonical/OG/title 均正确；站点构建测试单独证明共享文件搬移与宿主分流，组织站无 JS 回归保留。发现的既有 dev StrictMode/Helmet 兼容债另记任务 F1，不以开发态现象替代生产构建验收，也不将本地运行当作部署证明。
