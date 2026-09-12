# 账号注册与登录

本指南说明 ZenStory 工作台的邮箱注册、验证、邀请、登录和账号恢复入口，以及取决于部署配置的行为。

ZenStory 工作台的注册、登录和账号入口位于 [app.zenstory.ai](https://app.zenstory.ai)。组织介绍与本指南位于 zenstory.ai；使用下方工作台链接，不必从组织首页寻找注册按钮。

> **源码核对：2026-09-12。** 本文依据文末固定版本源码说明网页行为，**不是线上账号验收**。没有创建账号、验证邮件投递或测试真实 Google 授权。部署开关、邀请码规则和验证码倒计时以当前页面及服务端返回为准。

## 邮箱注册

1. 打开工作台的 [注册页面](https://app.zenstory.ai/register)。
2. 填写用户名、邮箱、密码和确认密码。当前网页表单要求用户名至少 3 个字符、密码至少 6 个字符，且两次密码一致。
3. 按表单提示填写邀请码：是否必填由当前注册策略决定，页面在提交前会再次读取策略。不能把“可选”或“邀请制”当作所有部署的固定规则。
4. 阅读并勾选服务条款和隐私政策，然后提交注册。
5. 注册请求成功后，页面会转到邮箱验证入口。输入邮件中的验证码，验证成功后保存登录状态并进入 Dashboard。

注册接口成功返回不等于验证邮件已经送达。未收到邮件时先检查邮箱拼写和垃圾邮件，再按页面提供的冷却倒计时重新发送；验证码有效期和重发间隔以当前页面为准，不保证固定等待时长或投递时间。

## 邀请链接与奖励

邀请链接可以预填邀请码。下面仅演示 URL 格式，`ABCD-1234` **不是发放给你的有效邀请码**：

```text
https://app.zenstory.ai/register?invite=ABCD-1234
```

页面也兼容 `?code=` 参数，并对输入进行格式规范化。完整代码形如 `XXXX-XXXX`；输入完整后可查询验证结果。服务端检查代码是否存在、启用、过期以及是否还有可用次数。

- 具体使用上限、有效期和奖励以当前规则及邀请码记录为准；源码默认值不是当前部署的固定承诺。
- 邀请奖励在邮箱验证后处理，并可能受反滥用规则限制；注册或验证成功不等于奖励一定发放。
- 无效时检查复制内容和页面错误，必要时向提供者索取可用代码。不要把示例代码当作注册资格。

## Google 登录（取决于部署配置）

如果登录或注册页面显示 Google 按钮，可以按页面提示进入授权流程。按钮显示和后端 OAuth 配置是不同条件；本文不证明当前部署已经配置成功。

新 Google 用户仍需遵循当前邀请码策略。已核对源码会使用 Google 返回的信息创建新账号、标记邮箱已验证，并在可用时保存头像；这不是对账号创建、授权可用性或安全性的保证。不要仅因文档写有 Google 登录，就假定它在每个自托管部署都可用。

## 登录与登录状态

打开 [登录页面](https://app.zenstory.ai/login)，使用用户名或邮箱及密码登录。

普通密码登录在没有待继续的目标时，会尝试打开当前用户在本地保存的项目；没有有效记录时再从项目列表选取最近更新或创建的项目。没有项目或项目列表读取失败时回到 Dashboard，因此不是所有登录都固定打开“最近使用项目”。

当前网页将登录令牌和用户状态保存在浏览器本地存储中。普通 API 请求遇到未授权响应时，会尝试刷新后重试；刷新被拒绝或重试仍未授权时需重新登录。令牌时长由部署配置决定，自动刷新不保证始终无需重新登录。

用户菜单提供登出入口。当前前端登出清除本地登录状态；它不等同于立即撤销所有设备上的会话，也不保证每个入口都跳转到同一页面。使用共享设备后应主动登出，不要把关闭标签页当作登出。

## 忘记密码

从登录页进入 [忘记密码页面](https://app.zenstory.ai/forgot-password)。这个入口受功能开关控制：启用时显示联系支持的邮件入口，未启用时会返回登录页。以页面实际展示的联系方式为准，本文不承诺处理时效。

**已核对版本没有网页自助发送密码重置链接的流程，也没有设置内的改密表单。** 后端修改密码接口不等同于已提供网页按钮；请按当前页面提示处理。

遇到登录问题时，保留非敏感错误提示；不要在公开反馈中粘贴密码、验证码、邀请代码或登录令牌。本文不宣称提供双重认证、跨设备立即退出或指定支持渠道的服务保证。

## 已核对源码

以下链接固定到同一版本，供核对实现边界；它们不证明后续版本或线上配置保持不变。

- [注册链接与表单](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/web/src/pages/Register.tsx#L91-L237)
- [注册策略](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/server/api/auth.py#L115-L172)
- [验证成功与重发](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/web/src/pages/VerifyEmail.tsx#L153-L207)
- [邀请码验证](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/server/api/referral.py#L160-L209)
- [奖励配置默认值](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/server/services/features/referral_service.py#L31-L38)
- [邀请奖励反滥用检查](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/server/services/features/referral_service.py#L385-L426)
- [认证功能开关](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/web/src/config/auth.ts#L47-L68)
- [Google 新用户与邀请策略](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/server/api/oauth.py#L492-L591)
- [密码登录后的导航](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/web/src/pages/Login.tsx#L160-L247)
- [令牌保存与本地登出](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/web/src/contexts/AuthContext.tsx#L248-L282)
- [API 刷新与失败处理](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/web/src/lib/apiClient.ts#L124-L179)
- [忘记密码页面](https://github.com/zenstory-ai/zenstory/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/apps/web/src/pages/ForgotPassword.tsx#L8-L65)

## 下一步

- [创建第一个项目](./first-project.md)
- [了解工作界面](../user-guide/interface-overview.md)
- [与 AI 对话](../user-guide/ai-assistant.md)
- [管理文件](../user-guide/file-tree.md)
