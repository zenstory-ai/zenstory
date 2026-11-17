#!/usr/bin/env node
// Fails on high/critical npm advisories, except advisories listed in audit-allowlist.json.
// An allowlisted advisory is only accepted while it reaches no runtime dependency
// (checked with `npm audit --omit=dev`) and before its review date.
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'

const LEVELS = new Set(['high', 'critical'])
const allowlist = JSON.parse(readFileSync(new URL('../audit-allowlist.json', import.meta.url), 'utf8'))
const today = new Date().toISOString().slice(0, 10)

function advisories(args) {
  let out
  try {
    out = execFileSync('npm', ['audit', '--json', ...args], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })
  } catch (error) {
    out = error.stdout // npm audit exits non-zero when it finds anything
  }
  const report = JSON.parse(out)
  const found = new Map()
  for (const vuln of Object.values(report.vulnerabilities ?? {})) {
    for (const via of vuln.via ?? []) {
      if (typeof via !== 'object' || !LEVELS.has(via.severity)) continue
      const id = String(via.url ?? '').split('/').pop() || `${via.name}:${via.title}`
      found.set(id, { name: via.name, title: via.title, severity: via.severity })
    }
  }
  return found
}

const all = advisories([])
const runtime = advisories(['--omit=dev'])
const failures = []
for (const [id, adv] of all) {
  const entry = allowlist.find((item) => item.id === id)
  if (!entry) {
    failures.push(`${id} ${adv.severity} ${adv.name}: ${adv.title}`)
  } else if (runtime.has(id)) {
    failures.push(`${id} is allowlisted as dev-only but reaches a runtime dependency`)
  } else if (entry.review_by < today) {
    failures.push(`${id} allowlist entry expired on ${entry.review_by}; re-check for a patched release`)
  } else {
    console.log(`allowed (dev-only until ${entry.review_by}): ${id} ${adv.name}: ${entry.reason}`)
  }
}
if (failures.length) {
  console.error('High/critical advisories:\n' + failures.map((line) => `  ${line}`).join('\n'))
  process.exit(1)
}
console.log(`audit gate passed (${all.size} high/critical advisories, all allowlisted dev-only)`)
