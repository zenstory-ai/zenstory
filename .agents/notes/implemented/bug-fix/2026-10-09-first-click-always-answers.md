# Agent Note: 注册、引导保存和官网类型卡片的每一次点击都有回应

Status: implemented

## Problem

2026-10-09 第二轮新用户审计（N2，P1）：

- drama、serial 第一次点「创建账号」只发出 `GET /api/auth/register-policy`，没有 POST，也没有 toast；drama 连点两次无效，第三次才成功（`logs/register-requests.tsv`）。`Register.tsx` 的 `handleSubmit` 每次提交都先 await 一次 policy 请求再 POST；表单不完整时按钮是 `disabled`，点了什么也不发生，作者不知道缺什么。
- shortstory 第一次点「保存并进入工作台」没发 PUT。`OnboardingPersonaPage` 在 `!canSubmit` 时静默 return，按钮灰着但没写为什么；`saving` 是 state，连点两下时第二下在重渲染前就进来了。
- drama 点官网「短剧与网剧脚本」卡片，第一次「看起来没反应」，第二次才进注册页，而且注册页停在页面中间，标题被切掉。注册页是懒加载路由，导航要等 chunk 下载，期间官网卡片没有任何变化；浏览器保留了官网的滚动位置。
- 引导问卷「写作经验」默认选中「0-3 个月」，作者没回答也会按新手保存。

## Decision

**注册（`pages/Register.tsx`）**

- 挂载时预取一次不带身份的 policy。返回 `variant == "global_optional"` 或 `rollout_percent <= 0` 时答案与邮箱无关，存进 `globalPolicyRef`，之后不再按邮箱查询。
- 否则沿用「用户名和邮箱都有效时按身份查询」的 effect，查询的 promise 和结果按 `邮箱 + 用户名` 存进 `identityPolicyRef`。
- 提交时 `resolveSubmitPolicy()`：已知答案同步返回，直接 POST；同一身份的查询还在进行就复用那一个 promise，不再发新请求；查询失败或身份变了才发新请求，失败时退回当前 state（与原来一致）。
- 提交按钮只在请求进行中（显示「正在创建账号...」）和成功后禁用。表单不完整时按钮可以点，点了按顺序校验用户名、邮箱、密码长度、密码字节数、两次密码、服务条款，再查邀请码；每个提前 return 的分支都通过 `rejectSubmit` 同时写内联错误（`#register-form-error`）和 toast，并把焦点移到对应输入框。服务条款检查放到网络请求之前。邮箱格式错误新用现有的 `auth:errors.invalidEmail`。
- 注册页挂载时 `window.scrollTo({ top: 0 })`。

**引导页（`pages/OnboardingPersonaPage.tsx`）**

- 保存按钮在没选身份时仍禁用，但按钮下方显示原因 `onboarding:actions.needPersona`「先选一个创作者类型，或点「跳过」」，按钮 `aria-describedby` 指向它；`handleSubmit` 走到这个分支时也弹 toast。
- `savingRef` 同步拦住连点，`saving` state 只负责显示。
- 写作经验初始为未选（null）。没选时请求里不带 `experience_level`，由服务端填默认值 `beginner`（`api/persona.py` 的 `PersonaOnboardingUpsertRequest` 默认值，没改）；本地缓存用服务端返回值，缺失时按同一默认值写入。预览面板的经验提示只在选了之后出现。已保存过的资料照常带回上次的选择。

**官网类型卡片（`pages/HomePage.tsx`）**

- 点击后该卡片 `aria-busy=true`，右下角箭头换成转圈并常显，卡片内显示 `home:projectTypes.opening`「正在打开…」（`role="status"`），直到注册页接管。鼠标进入卡片时也预加载注册路由（原来只在 focus 时）。作品类型偏好的写入顺序不变（见 `feature/2026-10-09-landing-type-and-onboarding-promise.md`）。

## Alternatives considered

- **保留「表单不完整就禁用按钮」，只去掉提交时的 policy 等待。** 最强理由：改动最小，现有单测和 e2e 都按禁用断言。被否：审计里作者「按钮亮着点了没反应」和「按钮灰着不知道缺什么」都是同一种体验；禁用按钮本身不能告诉作者原因，内联提示（如密码长度）只在输入后才出现，空字段时什么也不说。
- **提交时一律不查 policy，只用页面上已有的 state。** 最强理由：最快，零等待。被否：灰度放开邀请码时答案依赖邮箱；作者刚改完邮箱就点提交，effect 的查询还没回来，state 是旧邮箱的答案，可能放行一个本应要求邀请码的注册（服务端会拒，作者看到的是一次失败）。复用同一身份的 in-flight 查询既不多发请求，也不会用错答案。
- **写作经验做成必答。** 最强理由：数据更完整。被否：引导页的说法是「回答 3 个问题，也可以直接跳过」，多一道必答题就是多一道门槛；服务端已有默认值，不答和今天的默认行为一致，只是界面不再替作者选。
- **在全局路由层加「切换路由时滚回顶部」。** 最强理由：一次解决所有页面。被否：工作台、文档页有自己的滚动记忆（`Layout.scrollMemory`），全局重置会打破它们；本次只有注册页有这个投诉。

## Consequences

- 收益：policy 已知时第一次点击直接发出注册请求；任何一次没提交成功的点击都告诉作者原因；引导页不会再「点了没反应」，连点只保存一次；官网卡片点下去立刻有反馈，注册页从顶部开始；经验题不再被默认填成新手。
- 代价：注册页每次打开多一次不带身份的 policy 请求。表单不完整时按钮看起来可点，作者可能先点再看提示。`auth.spec.ts` 里三个「按钮禁用」用例改为点击后断言 `#register-form-error`。没选经验的作者在服务端仍记为 `beginner`，分析数据里分不出「选了新手」和「没答」。
- 审计没能确认 N2 的根因（也可能是自动化工具点击没送达）；本次修的是两条已确认的「无回应」路径（提交前等待 policy、禁用按钮无说明）。真机埋点复现仍待做。

## Verification

`pnpm --dir apps/web exec vitest run src/pages/__tests__/Register.test.tsx src/pages/__tests__/Register.lifecycle.test.tsx src/pages/__tests__/OnboardingPersonaPage.test.tsx src/pages/__tests__/HomePage.test.tsx`：

- Register：policy 已知（按身份或全局）时第一次点击就 POST，policy 请求数不增加；身份查询还在进行时提交复用它、按钮禁用且连点只注册一次；邀请码必填的回答写进内联错误，补上邀请码后不再查询直接 POST；policy 两次网络失败时退回本地默认；空表单、邮箱格式错误、缺邀请码点击都有内联错误和 toast；StrictMode 下同样成立。
- Onboarding：首次进入三个经验选项都未选；不选经验时请求不带 `experience_level`；没选身份时显示原因且按钮 `aria-describedby` 指向它，选了之后原因消失；连点保存只发一次。
- HomePage：点击类型卡片后该卡片 `aria-busy` 并显示「正在打开」，其他卡片不变。
