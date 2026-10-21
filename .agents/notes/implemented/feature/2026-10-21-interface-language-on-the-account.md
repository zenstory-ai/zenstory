# Agent Note: 界面语言存在账号上，换设备登录沿用

Status: implemented

关联：[app-follows-browser-language](2026-10-21-app-follows-browser-language.md)（没有账号语言时的检测顺序）。

## Problem

2026-10-21 英文新用户诊断：作者在 A 浏览器切到英文、写了一篇英文稿，换到全新浏览器直接打开 `/login` 登录后，整个工作台都是中文，只有作者自己建的文件夹名是英文。原因是语言只存在浏览器的 localStorage 里；注册接口收到的 `language` 只用来发验证邮件，没有存下来。

## Decision

- **存储**：新表 `user_preference`（`user_id` 主键、外键指向 `user`；`ui_language` 可空、最长 8；`updated_at`）。迁移 `20261021_100000` 只新建表。
  - 不在 `user` 表加列：staging 由 `create_all` 建成、没有 Alembic 记录，加列要先手工执行 DDL，否则所有读用户的查询都会报 `UndefinedColumn`。新表在 API 启动时由 `init_db` 自动补建，旧代码也不读它。
- **写入**：
  - 注册时把请求里的 `language` 写进去，与建用户同一个事务。
  - 登录后作者主动切换语言时，前端调用 `PUT /api/auth/me/preferences {ui_language}`；只接受 `zh` / `en`（`en-US` 归一为 `en`），其他值忽略并返回已存的值。
- **读取**：`ui_language` 随已有的用户数据一起返回，前端不多发请求。
  - 返回它的接口：登录、刷新令牌、邮箱验证返回的 `user`，以及 `GET/PUT /api/auth/me`。
  - 读失败（表缺失、数据库错误）时回滚该次读取、记警告并按「没有」处理，不让登录和 `/me` 失败。
- **前端**（`lib/accountLanguage.ts`，由 `AuthContext` 在登录用户变化时启动）：
  - 账号有语言且和当前不同时切过去；之后作者每次切换都存回账号。
  - 账号还没有语言（老用户、Google 注册）时不主动写入，作者第一次切换时写入。
  - 退出登录后停止同步。
- **优先级**：
  - 已登录时账号语言优先于本机偏好和 `?lang=`；未登录时仍是 `?lang=` → 本机偏好 → 浏览器语言。
  - 已有账号语言的作者在登录页先切了语言、或从官网带 `?lang=` 进来，登录后仍回到账号语言；需要在登录后再切一次。前端分不清「检测到的语言」和「作者刚切的语言」（两者都缓存在同一个 localStorage 键里），这是有意的取舍。
- **切换后立即生效**：切换时同步更新内存里的登录用户和本机用户缓存（5 分钟有效期），缓存期内刷新不会回到旧语言。保存请求按先后顺序发出，最后一次切换为准。
- **并发**：两个首次保存同时建行时，后到的捕获 `IntegrityError`，回滚后改为更新。注册时传入不支持的语言什么也不做，也不读数据库，不会回滚注册事务。

## Alternatives considered

- **`user` 表加 `ui_language` 列**。最强理由：读写最直接，不用多一张表。没有采用：staging 没有 Alembic 记录，生产需要在发布前先迁移；任何一步漏掉，读用户的查询都会失败，相当于登录全面中断。新表即使漏迁移，也只是读不到偏好。
- **单独 `GET /api/auth/me/preferences`，登录后再拉一次**。最强理由：用户数据的结构不变。没有采用：每次登录和启动都多一次请求；现有前端测试逐条断言登录前后的请求序列，多一次请求说明它确实改变了网络行为。随用户数据返回不需要额外往返。
- **老用户和 Google 注册用户进来时，自动把当前语言写进账号**。最强理由：账号语言更快覆盖所有人。没有采用：老中文用户第一次在英文浏览器的新设备上登录时，会被写成英文。只记录作者明确的选择（注册时的界面语言、主动切换）更可靠。

## Consequences

- 收益：
  - 英文作者在手机、新电脑上登录直接是英文。
  - 已经存了账号语言的中文作者（新注册的作者，或切换过一次语言的作者），不会因为新设备的浏览器语言被切成英文。
  - 服务端以后可以按账号语言发邮件或生成文案。
- 代价：
  - 多一张表和一条迁移。
  - 生产发布前要按 `docs/ops/release-ci-cd.md` 的迁移流程执行 `alembic upgrade head`（只新建表，向后兼容）。staging 由启动时的 `init_db` 自动建表。
  - 登录和刷新多一次按主键的读取。
  - 还没有账号语言的老账号，在浏览器语言为英文的新设备上会先按浏览器语言显示英文，直到作者切换一次（此后账号记住）。没有自动回填，见上面被否的方案。

## Verification

- `cd apps/server && pytest tests/test_api/test_user_preferences.py tests/test_models/test_user_preference_migration.py tests/test_api/test_auth.py tests/test_api/test_auth_dependency_worker.py tests/test_api/test_auth_route_worker.py -q --no-cov`：注册写入、`/me` 与登录带回、切换存回、不支持的语言被忽略、迁移可往返。
- `cd apps/web && pnpm exec vitest run src/lib/__tests__/accountLanguage.test.ts src/contexts/__tests__/`：新设备沿用账号语言且不回写、切换后存回、退出后停止。
