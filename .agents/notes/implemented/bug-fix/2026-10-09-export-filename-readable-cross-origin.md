# Agent Note: 导出文件名跨域可读，下载名按 RFC 6266 解析

Status: implemented

## Problem

2026-10-09 第二轮新用户审计（N10）：导出的文件叫「导出.txt」，不是项目名。`api/export.py` 已经返回 `Content-Disposition: attachment; filename*=UTF-8''{项目名}_正文.txt`，但工作台（`app.zenstory.ai`）和 API（`api.zenstory.ai`）跨域，`main.py` 的 CORS `expose_headers` 只有 `X-Request-ID`、`X-Trace-ID`、`X-Agent-Run-ID`，浏览器不让脚本读 `Content-Disposition`，`lib/api.ts` 只能退回「导出.txt」。

前端的解析也不严：`/filename\*=UTF-8''(.+)/` 会把后面的 `; filename="…"` 一起吞进文件名，大小写敏感，不认语言标签和引号；技能导出另有一份写法略不同的正则。

## Decision

- `main.py` 的 `CORSMiddleware(expose_headers=[…])` 加上 `Content-Disposition`（只出现一次）。
- 新增 `apps/web/src/lib/contentDisposition.ts` 的 `parseContentDispositionFilename(header)`：按分号拆参数，支持带引号和 `\"` 转义的值；优先 `filename*`（charset 不区分大小写，支持 UTF-8 和 ISO-8859-1，允许语言标签），解码失败时退回 `filename`；去掉路径部分和控制字符；没有可用名字时返回 null。
- `exportApi.exportDrafts` 和 `skillsApi.exportSkill` 都改用它，各自保留原来的兜底名（「导出.txt」/ `Export.txt`、`{技能名}.zip`）。

## Alternatives considered

- **前端自己按项目名拼文件名，不读响应头。** 最强理由：不依赖 CORS 配置，部署错开时也能拿到项目名。被否：文件名规则（`_正文` 后缀、扩展名）属于导出服务，前端再拼一份会和服务端分叉；以后加 docx 或分卷导出时还得同步改两处。
- **`expose_headers=["*"]`。** 最强理由：以后新加的响应头不用再改配置。被否：带凭据模式的请求里浏览器把 `*` 当字面量而不是通配符，是否生效取决于每个 fetch 的 `credentials` 设置；而且会把所有响应头暴露给任何允许的源，超出需要。

## Consequences

- 收益：导出的文件名是「{项目名}_正文.txt」；两处下载共用一个解析器，`filename*` 后面跟别的参数也不会污染文件名。
- 代价：每个跨域响应多一个暴露头（只是允许读取，不新增数据）。服务端改动需要 Railway 部署后才生效，之前前端仍会退回兜底名。

## Verification

- 后端：`venv/bin/python -m pytest tests/test_api/test_export.py -q --no-cov -n 0`：带允许的 `Origin` 请求导出，响应的 `access-control-expose-headers` 含且只含一次 `content-disposition`，`content-disposition` 为 `attachment; filename*=UTF-8''` 加 URL 编码的「雨夜_正文.txt」。
- 前端：`pnpm --dir apps/web exec vitest run src/lib/__tests__/contentDisposition.test.ts src/lib/__tests__/api.test.ts`：导出接口原样的头解出「雨夜_正文.txt」；`filename*` 前后有 `filename` 都取 `filename*`；语言标签、引号、转义、`filename=` token 形式；`filename*` 编码损坏时退回 `filename`；路径被去掉、没有名字时返回 null。
