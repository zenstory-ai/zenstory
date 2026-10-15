# 域名与静态页面路由

组织站提供开源项目介绍、指南与文档，工作台用于写作，API 负责数据与任务。既有公开链接通过重定向继续可用，登录状态按 origin 隔离。

## 当前域名

| 环境 | 组织站 | 工作台 | API |
| --- | --- | --- | --- |
| 生产 | `zenstory.ai` | `app.zenstory.ai` | `api.zenstory.ai` |
| 预发布 | `geo-preview.zenstory.ai` | `app-preview.zenstory.ai` | `staging-api-staging-a289.up.railway.app` |

预发布保留登录保护与 noindex，使用独立数据和凭证。访问健康页不证明用户会话或所有集成正常。

## 路由约定

- 组织首页、项目介绍、术语、文档和法律页面在组织站静态生成。
- `/docs` 两边各自保留阅读入口，共用 `apps/web/docs` 内容源。工作台使用 React 阅读器、搜索与语言设置，组织站使用静态双语页面。
- 工作台上的其他组织内容入口跳到组织站；组织站旧的登录、项目编辑、管理后台等入口跳到工作台，保留路径和查询参数。
- 法律页面的品牌入口返回工作台；组织内容的品牌入口返回组织首页。
- OAuth 回调重定向保留必要片段，随后由回调页清理凭证；不要手动把登录令牌搬进 URL。
- 生产与预发布 `/api/*` 分别代理到对应 API。实际健康入口是 API origin 的 `/health`，不是 `/api/health`。

跨 origin 的浏览器存储不共享，域名切换后可能需要重新登录。不要复制用户令牌来“迁移会话”。

## 构建来源

路由定义在 `apps/web/content/site-routing.json`，域名布局由 `apps/web/scripts/build-site-layout.mjs` 生成。`vercel.json` 必须与生成结果一致：

```sh
pnpm --dir apps/web build:vercel
```

只有修改路由契约时才重新生成跟踪配置：

```sh
pnpm --dir apps/web exec node scripts/build-site-layout.mjs --write-config
```

普通 Vite build 与 Docker 保留 app 根入口；Vercel finalizer 移动组织、工作台布局并处理 robots 和 sitemap。私有应用路由不进入 sitemap。组织站的公开文档和法律页不加载工作台 bundle，保留单 URL 双语阅读。Vercel 将静态文档放到 `_site/docs` 并按组织域名重写，App 的 `/docs` 使用应用壳；公开文档 canonical 仍指向组织站。

## 验证

本地生成器测试验证静态链接、metadata 和路由；实际 provider 配置仍需单独读取。域名浏览器检查不默认访问线上：

```sh
# 从 apps/web 运行；目标由模式和测试配置限定
GEO_DOMAIN_MODE=preview pnpm test:geo
```

生产模式只在已授权发布后使用。检查包括匿名页面、重定向、无 JS 阅读、noindex 和无凭证的错误回调，不创建账号或调用模型。

## 发布与回退

按[发布流程](release-ci-cd.md)验证 main、预发布及生产 tag。操作前记录当前 provider 来源与必要配置，不复用历史 deployment ID 作为今天的回退基线。

发生路由回归时，根据当前记录回退受影响配置或部署，保留兼容旧链接的重定向。域名调整不授权改数据库、删除上传卷或更换用户凭证。
