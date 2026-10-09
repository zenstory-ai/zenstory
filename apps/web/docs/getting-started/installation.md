# 账号注册与登录

本页说明 ZenStory 工作台的注册、邮箱验证、邀请、登录和找回密码。注册和登录都在 [app.zenstory.ai](https://app.zenstory.ai)，不在组织首页 zenstory.ai。

## 邮箱注册

1. 打开工作台的[注册页面](https://app.zenstory.ai/register)。
2. 填写用户名、邮箱、密码和确认密码。用户名至少 3 个字符，密码至少 6 个字符，两次密码要一致。
3. 如果页面有邀请码栏，按提示填写；标着「可选」的可以留空。
4. 阅读并勾选服务条款和隐私政策，然后提交注册。
5. 提交后页面会转到邮箱验证。输入邮件里的验证码，验证成功后直接进入工作台。

没收到验证邮件时，先检查邮箱拼写和垃圾邮件，等页面上的倒计时结束后再重新发送。

## 邀请链接与奖励

邀请链接会自动填好邀请码，格式如下（`ABCD-1234` 只是示例）：

```text
https://app.zenstory.ai/register?invite=ABCD-1234
```

页面也认 `?code=` 参数。完整的邀请码形如 `XXXX-XXXX`，填完后页面会检查它是否存在、是否过期、是否还有剩余次数。

- 好友用你的邀请码注册并验证邮箱后，你们都能获得积分，积分可以兑换 Pro。
- 异常注册可能拿不到奖励。
- 邀请码无效时，检查复制是否完整，或向提供者要一个新的。

## Google 登录

登录或注册页面上有 Google 按钮时，点它按提示授权即可。用 Google 新建的账号同样按当前的邀请码规则注册，邮箱自动视为已验证。自托管部署要先配置 Google OAuth 才能用。

## 登录与登录状态

打开[登录页面](https://app.zenstory.ai/login)，用用户名或邮箱加密码登录。

登录后会打开你在这台设备上最近用过的项目；没有记录时打开最近更新的项目，还没有项目就回到工作台。

登录状态保存在浏览器本地，过期时会自动刷新；刷新失败就需要重新登录。

用户菜单里可以登出。登出只清除这台设备上的登录状态；在共享设备上用完请主动登出，只关掉标签页不算登出。

## 忘记密码

从登录页进入[忘记密码页面](https://app.zenstory.ai/forgot-password)，按页面上的联系方式处理；在线版请用注册邮箱写信到 support@zenstory.ai。

**目前没有网页自助发送密码重置链接的流程，设置里也没有改密表单。**

反馈登录问题时，不要贴出密码、验证码、邀请码或登录令牌。

## 已核对源码

以下链接指向本文核对时的源码版本。

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
