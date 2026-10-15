# Historical Test Refactoring Plan

This plan and its counts were imported in commit `3ef311e6` on 2026-04-29. The checklist below records that historical plan. For current test commands and conventions, use the [E2E README](README.md) and the [contribution guide](../../../CONTRIBUTING.md).

## Summary
- **Total waitForTimeout occurrences**: 360+
- **Files to refactor**: 13
- **Status**: In progress

## Completed
- [x] Updated constants.ts with additional timeout constants

## Files to Refactor

### Priority 1 (Target Files)
1. **files.spec.ts** - 103 occurrences
   - [ ] Replace hardcoded timeouts with constants
   - [ ] Replace waitForTimeout with assertions where possible
   - [ ] Add Given-When-Then comments to complex tests

2. **chat.spec.ts** - 3 occurrences
   - [ ] Replace hardcoded timeouts
   - [ ] Add Given-When-Then comments

3. **versions.spec.ts** - 74 occurrences
   - [ ] Replace hardcoded timeouts
   - [ ] Add Given-When-Then comments

4. **projects.spec.ts** - 2 occurrences
   - [ ] Replace hardcoded timeouts
   - [ ] Add Given-When-Then comments

### Priority 2 (Other Files)
5. **accessibility.spec.ts** - 12 occurrences
6. **admin.spec.ts** - 23 occurrences
7. **export.spec.ts** - 51 occurrences
8. **materials.spec.ts** - 1 occurrence
9. **public-skills.spec.ts** - 28 occurrences
10. **skills.spec.ts** - 36 occurrences
11. **voice.spec.ts** - 21 occurrences
12. **chat.mocked.spec.ts** - 2 occurrences
13. **helpers/common.ts** - 4 occurrences

## Refactoring Rules

### 1. Replace waitForTimeout with Assertions
**BEFORE:**
```typescript
await page.waitForTimeout(500)
await expect(element).toBeVisible()
```

**AFTER:**
```typescript
await expect(element).toBeVisible({ timeout: TIMEOUTS.FILE_OPERATION })
```

### 2. Replace Hardcoded Timeouts
**BEFORE:**
```typescript
await page.waitForSelector('.overflow-auto', { timeout: 5000 })
await page.waitForTimeout(300)
```

**AFTER:**
```typescript
await page.waitForSelector('.overflow-auto', { timeout: TIMEOUTS.MEDIUM })
await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND) // Only for UI transitions
```

### 3. Keep waitForTimeout Only When Necessary
Valid use cases for waitForTimeout:
- Testing time-based features (idle timeout, debounce timing)
- No alternative assertion available
- Documented reason in comment

**Example:**
```typescript
// Wait for idle timeout (10 seconds in app) to trigger suggestions
await page.waitForTimeout(TIMEOUTS.IDLE_SUGGESTION)
```

### 4. Add Given-When-Then Comments
Add to complex tests (3+ steps):
```typescript
test('user can create a new file', async ({ page }) => {
  // Given: User is logged in and on a project page
  // When: User creates a new file
  // Then: File appears in the file tree
})
```

## Acceptance Criteria
- [ ] 0 hardcoded waitForTimeout (except time-based tests)
- [ ] All timeouts use constants from constants.ts
- [ ] Skipped tests documented
- [ ] Given-When-Then comments added (10+ tests)
- [ ] Tests more stable and maintainable
- [ ] TypeScript compiles
- [ ] ESLint passes

## Progress Tracking
- **Files completed**: 0/13
- **waitForTimeout replaced**: 1/360+
- **Given-When-Then added**: 0/10+
