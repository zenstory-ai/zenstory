# Agent Note: 客户端 IP 只信任代理追加的那一段，Cloudflare 头需经来源校验

Status: implemented

## Problem

`middleware/rate_limit.get_client_ip` 优先取 `X-Real-IP`，其次取 `X-Forwarded-For` 最左边的第一个合法 IP。这两个值客户端都能自己带上：攻击者每次换一个伪造 IP，就能绕过登录 IP 限流（`auth_login_ip`）、兑换码限流，并在管理员审计日志与请求日志里写入任意 IP。uvicorn 的 `--forwarded-allow-ips` 也救不了，因为函数直接读请求头。`api/auth.py` 注册时记录 referral 反作弊 IP 也是同样取 XFF 最左。

## Decision

`get_client_ip(request)` 的顺序：

1. **`CF-Connecting-IP`**，仅当请求确实来自 Cloudflare。`CLIENT_IP_CLOUDFLARE`：`auto`（默认）——下一步得到的代理对端 IP 落在 Cloudflare 公布的网段内（`_DEFAULT_CLOUDFLARE_IP_RANGES`，可用 `CLOUDFLARE_IP_RANGES` 逗号分隔覆盖）才采用；`always`——只要有就采用；`off`——忽略。
2. **代理追加的 `X-Forwarded-For` 段**：`TRUSTED_PROXY_HOPS`（默认 1）表示前面有几层会往 XFF 右侧追加的代理，取从右数第 `hops` 个；条目数少于 hops 时取最左（此时全部条目都由可信代理写入）；`0` 表示不看 XFF。该段不是合法 IP 时回退到 socket 对端。
3. **socket 对端** `request.client.host`，仍无则 `"unknown"`。

`X-Real-IP` 不再被读取。注册接口的 referral IP 改用 `get_client_ip`。所有使用方（登录/兑换限流、管理员审计、请求日志 `client_host`、OAuth referral）共用这一函数。

生产需要确认的设置（代码无法判断拓扑）：

- 只经 Railway 边缘直连（默认）：`TRUSTED_PROXY_HOPS=1`，Railway 边缘把连接它的地址追加在 XFF 最右。可用 `curl -H 'X-Forwarded-For: 1.2.3.4' -H 'X-Real-IP: 1.2.3.4' https://<api>/...` 后查看请求日志的 `client_host`，应为本机出口 IP 而不是 1.2.3.4。
- `api` 域名开了 Cloudflare 代理（橙云）：保持 `CLIENT_IP_CLOUDFLARE=auto`。此时 XFF 最右是 Cloudflare 边缘 IP，`auto` 会识别并改用 `CF-Connecting-IP`；如果没识别（例如 Cloudflare 更新了网段），所有用户会按 Cloudflare 边缘节点共用限流桶，应更新 `CLOUDFLARE_IP_RANGES`。同时建议 Railway 只接受来自 Cloudflare 的流量，否则直连 `*.up.railway.app` 不受 Cloudflare 防护。
- 前端经 Vercel rewrite 转发 API：需要把 `TRUSTED_PROXY_HOPS` 设为 2（Vercel + Railway），否则所有人都会按 Vercel 出口 IP 共用限流。目前前端通过 `VITE_API_BASE_URL` 直连 API，不经 rewrite。

## Alternatives considered

- **保留 X-Real-IP 优先**。最强理由：Railway 文档说明边缘会设置它，零配置可用。被否：无法从代码确认 Railway 是覆盖还是透传客户端带来的值；XFF 右侧追加是代理的通用语义，信任边界可以由配置明确表达。
- **默认 `TRUSTED_PROXY_HOPS=0`（只信 socket 对端）**。最强理由：最保守，不信任任何头。被否：应用在 Railway 边缘之后，socket 对端是代理地址，所有用户会共用同一个限流桶，登录限流等于对全站生效。
- **`CF-Connecting-IP` 只用配置开关，不校验来源**。最强理由：实现简单，不维护 Cloudflare 网段。被否：开关打开后，绕过 Cloudflare 直连源站的请求可以随意伪造该头；校验对端网段让默认值在两种拓扑下都安全。
- **启用 uvicorn `--proxy-headers --forwarded-allow-ips`**。最强理由：交给服务器处理，`request.client` 直接就是客户端。被否：Railway 边缘地址不固定，只能写 `*`，等于信任所有 XFF；也处理不了 Cloudflare 这一层。

## Consequences

- 收益：客户端无法通过请求头选择自己的限流键或伪造审计 IP；Railway 直连与 Cloudflare 代理两种拓扑在默认配置下都能拿到真实客户端 IP。
- 代价：拓扑变化（加 Vercel rewrite、加一层代理）必须同步调整 `TRUSTED_PROXY_HOPS`，否则会退化为共用限流桶；Cloudflare 网段写在代码里，变更时需要更新或用环境变量覆盖。依赖 `X-Real-IP` 的测试改为 `X-Forwarded-For`。
