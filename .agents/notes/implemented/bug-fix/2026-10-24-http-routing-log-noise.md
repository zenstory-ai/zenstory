# Agent Note: 保留请求完成日志，减少重复的路由诊断

Status: implemented

## Problem

只读审计中一次扫描窗口出现大量不存在路径的 404/405；请求开始、HTTPException、请求完成可能为同一次请求各写一条。平台记录过日志限流与丢弃，但没有证据证明这次修改就能消除所有限流。

## Decision

补充 [后端日志与错误管线](../architecture/2026-10-05-backend-log-and-error-pipeline.md)：请求开始日志改为 DEBUG；仅框架未匹配 endpoint 的 404 和已匹配路由但 method 不在其 methods 中的 405，将额外 HTTPException 诊断改为 DEBUG。每次请求的完成日志、原等级、耗时、request_id 保留。业务主动抛出的 404/405、认证、限流、5xx 和异常堆栈不降级；无法确定路由来源时保留 WARNING。

用真实 ASGI 路由测试普通、路由错误、业务错误、认证、限流、意外异常及 DEBUG 诊断，不改变响应或 header。

## Alternatives considered

- **全局降低 404/405**：代码更少，但会隐藏业务资源缺失和业务规则异常，故拒绝。
- **关闭 access log 或新增采样**：减量更大，但失去请求级完成证据或引入不必要机制，故保留现有日志并只去重复。

## Consequences

正常请求与扫描路由在 INFO 层各保留一条完成记录；排障仍可开启 DEBUG。此次未修改云日志配置，不承诺峰值丢弃归零。

## Verification

12 个 ASGI 回归用例在修复前有 5 个行为失败；修复后与既有 middleware、error handler、structured logging 测试共 53 passed。既有 4xx completion 等级仍为 INFO，未为测试改动产品等级。Ruff 的 CI 范围通过；扩展扫描 `core/error_handler.py` 时存在原有 `APIException.__init__(**kwargs)` 的 ARG002，HEAD 原文件同样复现，未顺手修改兼容签名。
