# 上传存储的发布验收探测与脱敏失败分类

## Problem

2026-10-21 Railway 因安全事件重置全部 Bucket 凭据。生产 `server` 的 `UPLOAD_S3_ACCESS_KEY_ID` 与当前 bucket 一致，`UPLOAD_S3_SECRET_ACCESS_KEY` 仍是旧值：应用签名请求返回 `403 SignatureDoesNotMatch`；同一探测只换新 secret、其他参数与签名实现不变，随机不存在对象返回 `404 NoSuchKey`。材料上传因此失败，用户只看到通用 503。修复只更新该 secret，并从原源码 `b0ee117e` 重新部署。

现有检查都没有发现它：`/health/ready` 只查 DB 与 Redis；`release_preflight_check.py` 不看上传存储；`UploadStorageSettings` 只验证变量完整和格式，变量非空不等于凭据有效。`_signed_request` 把所有非 2xx 都变成同一条 `private object storage {method} failed`，日志里没有任何可区分的原因；GET 下载中途断网还会以 `httpx.ReadError` 漏出存储边界，变成 500。

## Decision

- `S3UploadStorage._signed_request` 对所有方法都以流式接收响应（此前 PUT/DELETE 会先完整缓冲响应体），请求带不签名的 `Accept-Encoding: identity`。失败时只用 `iter_raw()` 读取原始响应的前 4KiB（最多多拉一个 transport 读块），读完即在 `finally` 中关闭连接；响应声明了非 identity 的 `Content-Encoding` 时不解压、不读取，只按状态码分类，避免小压缩体膨胀成无限缓冲。前缀中的 S3 `<Code>` 映射为固定分类：`SignatureDoesNotMatch`→`signature`；`InvalidAccessKeyId`、`AccessDenied` 等及其余 401/403→`auth`；`RequestTimeTooSkewed`→`clock`；`NoSuchBucket`、`AuthorizationHeaderMalformed`、`PermanentRedirect`→`config`；连接失败与下载中断→`network`；其余（含 5xx）→`provider`。`UploadStorageError` 带 `reason` 与 `status`，每次失败记一条 WARNING，只含方法、分类、HTTP 状态和白名单内的 S3 code，不记响应体、URL、key 或凭据。GET 404 仍是 `UploadNotFoundError` 且不记日志，但 `NoSuchBucket` 改为 `config` 存储错误。对用户的响应保持通用 503。
- `S3UploadStorage.probe_round_trip()` 在 `preflight/<32 hex>.bin` 用无覆盖 PUT 写 64 个随机字节，GET 读回比对 SHA-256（读回超长或不符都记为 `integrity`），再 DELETE，返回步骤、分类、清理结果和合成 key。PUT 被 4xx（含 412）拒绝时不发 DELETE：没有写入，也绝不删除不属于本次探测的对象；网络错误或 5xx 时写入结果未知，仍尝试 DELETE。清理失败本身也算失败。
- `scripts/release_preflight_check.py` 新增 `--probe-upload-storage`，始终校验 `UPLOAD_*` 配置；local 后端按既有契约直接通过，不发网络请求；`--strict` 时 S3 后端未带探测参数即失败，`make preflight-release` 同步带上该参数。失败行给出分类与操作提示，清理失败时打印合成 key 供手工删除。
- 运维步骤写在 `docs/ops/release-ci-cd.md#upload-storage-acceptance`：打 `prod-v*` tag 前用 `railway run --service server --environment production` 从发布源码跑一次；新部署上线后用 `railway ssh` 在容器里再跑一次；任何 bucket 凭据或 `UPLOAD_*` 变量变化（包括平台在发布之外发起的重置）后重新部署并重跑。

## Alternatives considered

- **把存储探测放进 `/health/ready`**。最强理由：Railway 每次部署都会自动执行，不依赖人记得跑命令，本次事故若发生在部署时会被直接拦下。被否：外部 S3 的偶发抖动或限流会让整个 API 被摘除，聊天、编辑等不碰上传的功能一起中断；就绪检查每 30 秒执行，持续写对象也不合适。仓库已有同样理由拒绝在 readiness 里做 schema 检查。
- **新增受保护的 HTTP 端点，在运行中的进程里做往返**。最强理由：精确覆盖已部署进程的代码与环境，可被外部监控调用。被否：需要新的鉴权面和滥用防护，一个可触发写对象的生产端点本身是风险；`railway ssh` 在容器内跑脚本已能覆盖同样的代码与环境。
- **只用签名 GET/HEAD 随机不存在的 key，以 404 判定通过**。最强理由：只读，不在 bucket 留任何痕迹，本次事故里新旧 secret 正好能区分出 404 与 403。被否：它只证明读签名有效，不能证明写权限、读回字节和删除权限，而上传路径首先需要 PUT。
- **按 HTTP 状态码分类（403 即密钥失效）**。最强理由：实现最简单，不需要解析响应体。被否：`AccessDenied`、`RequestTimeTooSkewed` 等同样是 403，会把权限策略或时钟问题误诊为密钥失效，误导运维去轮换本来有效的凭据。
- **定时任务持续探测并告警**。最强理由：能在发布之外的平台事件后尽快发现问题，正是本次缺失的能力。被否：需要新的调度、告警通道和持续写入，超出本次轻量检查的范围；先靠失败分类日志让下一次通用 503 能被快速定位。

## Consequences

收益：发布前与部署后的验收真实覆盖生产使用的存储配置与签名路径，旧 secret 会以 `signature` 明确失败；运行中出现的存储失败在日志里能区分签名、权限、时钟、配置、网络和服务商问题；下载中途断网不再变成 500。

代价：检查需要操作者显式执行，并具备生产 Railway 访问权限；`railway run` 会把生产变量注入操作者本机进程。每次探测在 bucket 写入并删除一个对象，清理失败时需手工删除。它是时点检查，无法阻止或发现检查之后发生的凭据重置，本次重置就发生在发布之外。失败响应最多读取 4KiB 加一个 transport 读块后即断开连接，该连接不再复用。
