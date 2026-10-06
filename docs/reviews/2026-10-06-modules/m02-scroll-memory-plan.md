# M02 mobile scroll namespace repair plan

The real Layout uses useScrollMemory(currentProjectId||default). Its documented
namespace/unknown-position0 contract must survive a mounted key change. First
exercise actual hook/native sessionStorage, saveA positions then rerender absentB;
repeat malformedB JSON and savedB/returnA/default/read/write-failure controls in
default and rootStrict. No fake renderer/hook or new validation/storage framework.
If genuine namespace inheritance RED, reset the existing ref to {} before loading
each new key. Preserve savedB, no writes on load, guarded parse/persist and in-memory
write-failure fallback. No account-content/privacy claim, no extra schema/array/
primitive migration, effects/handlers otherwise unchanged. Types/lint/scoped hook
coverage plus existingLayout tests and independent source review. Local only,
no Actions/server/browser/network/install/commit/push/provider operations.
