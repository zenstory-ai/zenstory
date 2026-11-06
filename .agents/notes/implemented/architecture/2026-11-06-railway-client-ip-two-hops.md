# Agent Note: Railway 上客户端 IP 在倒数第二段，生产 /api 回退不承载按网络计费的入口

Status: implemented

修正[客户端 IP 信任边界](2026-10-05-client-ip-trust-boundary.md)中关于 Railway 拓扑的事实；该记录的信任原则（只信代理追加的段、不读 `X-Real-IP`）不变。为[免费账号按客户端网络共享额度](../feature/2026-11-05-free-account-network-shared-quota.md)的前置条件。

## Problem

原记录写明「只经 Railway 边缘直连时 `TRUSTED_PROXY_HOPS=1`，边缘把连接它的地址追加在 XFF 最右」，这个前提没有实测过。实测（staging，日志的 `client_host` 与已知出口地址对比，均为合成未登录请求）：两个出口不同的来源在 hops=1 时解析到同一组 Railway 侧地址，不是客户端。生产日志的 `client_host` 也只有十几个不同值。这意味着登录 IP 限流一直按边缘地址分桶，而新的免费网络额度如果照此上线，会让全站免费账号共用少数几份额度。

同时，`apps/web/vercel.json` 把任意主机的 `/api/*` 转发到生产 API。经它到达时解析结果是轮换的云厂商出口：作者只换域名就能进入另一个桶。把 hops 调大也不能两全。

## Decision

- 托管环境显式设置 `TRUSTED_PROXY_HOPS=2`。这是实测结果，不是推断；代码默认值不变（自托管单层代理仍是 1）。`release_preflight_check.py --strict` 在变量缺失时失败，因为默认值在 Railway 上不报错、只会静默取错地址。
- 实测 hops=2（staging）：两个不同来源各自解析到自己的真实出口；客户端发送的单行、逗号分隔、两行、内网、空值、非法 `X-Forwarded-For`，以及 `X-Real-IP`、`CF-Connecting-IP`，都不能改变结果。Railway 边缘会移除客户端发送的 XFF，所以 `get_client_ip` 只读第一行不构成绕过，无需改代码。
- `site-routing.json` 的 `apiFallbackExcludedPaths` 列出按客户端网络计费的入口（`v1/agent/stream`、`v1/editor/natural-polish`），生产回退改写排除它们，这两条路径在生产主机上不再到达 API。其余路径（OAuth 发起、健康检查、CLI 文档等）继续走回退。服务端守卫测试要求新的计费入口同时列入清单。
- 线上核对发现路由规则只匹配未编码写法：`agent/%73tream`、`agent%2Fstream` 这类编码路径会绕过排除，经云厂商出口到达 API，由 uvicorn 解码后命中路由。因此两个计费路由还要求原始路径与解码后完全一致，否则 404（`require_canonical_request_path`）。自家客户端都不编码这些路径，路由排除和这项校验合起来才关闭这条路径。
- 生产构建已经直连 API 源站（核对过线上 bundle 和跨源预检），排除不影响正常客户端。没有配置 `VITE_API_BASE_URL` 的生产构建会在这两个入口上明确失败，而不是静默错分额度。
- Preview 构建走同源，其代理规则保持完整，否则预览环境无法使用 AI。Preview 经该路径的用户按云厂商出口分桶，这只影响测试环境。

## Alternatives considered

- **移除整条生产 `/api` 回退**。最强理由：一次关掉这类问题，登录限流也不会再经它错分。否决：OAuth 发起和健康检查的跨域冒烟测试明确依赖它，其他外部调用无法从仓库穷举，这次的目标是不破坏现有客户端。
- **保持 hops=1，改读 `X-Real-IP`**。最强理由：不用改配置。否决：客户端可以发送这个头，其覆盖行为没有证据，违背信任边界。
- **服务端识别 Vercel 特征头并拒绝**。最强理由：不依赖路由规则的匹配细节。否决：这些头客户端也能伪造，其是否转发到外部目标也没有证据；只用在拒绝方向虽然安全，但增加一条未实测的分支。

## Consequences

- 收益：免费网络额度按真实客户端计算；登录 IP 限流、管理员审计和请求日志也第一次拿到真实客户端地址。
- 代价：平台改变链路结构时结果会静默变错，上线后和平台变更后都要用已知出口地址比对 `client_host`。路由排除是名单制；编码写法由路由自身拒绝，线上用未登录请求核对（路由返回 404 而不是 401）。
- 用户自建代理等同于换网络，不在此规则能解决的范围内。

## Verification

`apps/web/scripts/__tests__/site-layout.test.mjs` 校验回退规则的放行与排除、其他规则不接手这些路径、Preview 规则保持完整；`apps/server/tests/test_api/test_free_ai_network_quota_api.py` 校验 Railway 链路形状下的分桶和排除清单；`test_release_preflight_check.py` 校验严格模式。线上探测的结论写在本记录，原始记录留在仓库外。
