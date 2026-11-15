# E2E 使用固定的兼容 Ubuntu 运行环境

## Problem

E2E 使用浮动的 `ubuntu-latest`。运行环境切换为 Ubuntu 26.04 后，锁文件固定的 Playwright 1.58.0 无法安装对应 Chromium，所有分片都在浏览器安装阶段失败，尚未执行产品测试。

## Decision

- 仅将 E2E 浏览器作业的运行环境固定为 `ubuntu-24.04`，保持现有锁文件和 Playwright 版本。
- 检测和汇总作业继续使用原环境；八分片、浏览器集合、测试范围、失败门禁和精确源校验保持不变。
- 本地运行既有 workflow 合同测试，独立复核该单行配置变更；合并后以精确 main 的完整 E2E 结果验收。

## Alternatives considered

- **升级 Playwright 以适应新系统。** 能跟进新的浏览器和系统支持，但同时会改变浏览器版本、快照和依赖范围；当前失败可以用已支持的运行环境解决，依赖升级应独立验证。
- **继续使用浮动标签并重跑。** 不改配置，但相同运行环境仍无法安装该版本浏览器，重复运行不能解决兼容性问题。
- **移除系统依赖安装。** 改动少，但会把安装失败推迟成浏览器缺少动态库，不能提供可靠验收。

## Consequences

- 测试恢复到该 Playwright 版本支持的系统，不影响产品运行环境或用户行为。
- 以后需要升级 Playwright 和运行环境时，应一起验证；本次不增加缓存、测试车道或部署 producer。

## References

- [Playwright 1.58 固定版本的 Ubuntu 24.04 浏览器下载定义](https://github.com/microsoft/playwright/blob/961381ec73a9fa2661d92504eab1a6bd6eaffc75/packages/playwright-core/src/server/registry/index.ts#L145-L152)。
- [GitHub 官方 runner 标签与运行环境说明](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#standard-github-hosted-runners-for-public-repositories)。
