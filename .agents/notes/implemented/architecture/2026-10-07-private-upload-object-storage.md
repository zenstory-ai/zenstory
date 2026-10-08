# 私有上传对象存储

## Problem

材料原文件与反馈截图依赖 API 本地卷。部署重叠受卷限制，反馈目录权限错误会直接导致上传失败；同时 worker 若长期缓存远端材料，会把容量问题转移到临时磁盘。迁移还必须保留旧引用的可恢复性，不能删除用户原文件。

## Decision

API 新增严格的 S3-compatible 存储边界，默认仍为本地存储。只有完整设置 `UPLOAD_STORAGE_BACKEND=s3`、endpoint、region、bucket、access key、secret 和 `virtual|path` URL style 才启用对象存储，配置错误直接失败。持久引用使用受限的 `s3://bucket/material/<user>/<opaque>` 与 `s3://bucket/feedback/<opaque>`；只有 API 持有凭据。PUT 使用 SigV4 和 `If-None-Match: *`，GET 有 20MB/5MB 上限。

worker 继续通过内部 token API 下载材料，每次 flow 使用独占临时源文件，并在成功或失败后删除。反馈截图仅管理员鉴权下载，列表只校验引用格式，不逐对象读取。旧本地引用继续按受信根目录读取。

迁移默认 dry-run，只处理数据库已引用、位于受信根目录且不含符号链接的文件，跳过运行中的材料任务。复制后用私有 GET 校验 size 与 SHA-256；切换数据库引用前，以 0600 原子文件和 fsync 写入恢复记录，再在同一事务更新 Novel、IngestionJob 或 UserFeedback。原文件不删除，恢复按当前新引用做条件切换。可用 `--user-id` 限定 staging 批次。

## Alternatives considered

- 继续扩容共享卷：无法解除 API 发布对单卷挂载的限制，也保留权限与容量风险。
- 让 worker 直接持有 bucket 凭据：扩大密钥暴露面，且绕过现有内部下载鉴权边界。
- 保存公开 URL 或签名 URL：会把短期授权信息写入数据库，并增加泄漏风险。
- 迁移成功后立即删本地文件：破坏可逆性，无法应对数据库提交结果不明确或回滚。

## Consequences

staging 可独立验证 PUT/GET/DELETE、API 鉴权、worker 字节一致性和迁移/恢复；生产仍需维护窗口与完整验收后才能启用。对象存储失败发生在 quota/DB 写入前；提交结果不明确时保留对象以防数据库已有引用。现有卷暂时保留，直到所有历史引用和其他持久目录完成独立盘点。

参考：[Railway Buckets](https://docs.railway.com/storage-buckets)、[AWS Signature Version 4](https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html)。

文档中的 `python scripts/migrate_upload_storage.py` 直接入口显式补上 server 导入路径；独立子进程清空 PYTHONPATH 并从临时目录执行 `--help` 的回归通过，避免 pytest 的自动路径配置掩盖实际运维启动失败。
