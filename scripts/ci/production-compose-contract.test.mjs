import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8')

test('production compose has no public state ports or known credential fallbacks', () => {
  const compose = read('docker-compose.full.yml')
  assert.doesNotMatch(compose, /POSTGRES_PASSWORD:\s*\$\{POSTGRES_PASSWORD:-/)
  assert.doesNotMatch(compose, /JWT_SECRET_KEY[^\n]*(?:change-me|changeme)/i)
  assert.doesNotMatch(compose, /(?:5432|6379):(?:5432|6379)/)
  assert.match(compose, /POSTGRES_APP_USER/)
  assert.match(compose, /POSTGRES_APP_PASSWORD/)
  assert.match(compose, /migrate:/)
  assert.match(compose, /service_completed_successfully/)
  assert.match(compose, /REDIS_PASSWORD/)
  assert.match(compose, /ENVIRONMENT:\s*production/)
  assert.match(compose, /JWT_SECRET_KEY:\s*\$\{JWT_SECRET_KEY:\?/)
})

test('production web image builds assets and serves them as a non-root user', () => {
  const dockerfile = read('apps/web/Dockerfile')
  assert.match(dockerfile, /FROM node:20-alpine AS build/)
  assert.match(dockerfile, /npm run build/)
  assert.match(dockerfile, /USER node/)
  assert.doesNotMatch(dockerfile, /npm.+run.+dev/)
})

test('database application role is separate and limited to runtime data access', () => {
  const init = read('apps/server/docker/init-app-role.sh')
  assert.match(init, /must be distinct/)
  assert.match(init, /GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES/)
  assert.doesNotMatch(init, /GRANT (?:USAGE, )?CREATE ON SCHEMA/)
  assert.doesNotMatch(init, /GRANT ALL PRIVILEGES/)
})

test('owner schema initialization registers payment models before creating tables', () => {
  const migrate = read('docker-compose.full.yml').split('  migrate:\n')[1].split('\n  redis:')[0]
  assert.match(migrate, /POSTGRES_OWNER_USER/)
  assert.match(migrate, /command:.*import models;.*from database import init_db;.*asyncio\.run\(init_db\(\)\)/)
  assert.match(read('apps/server/models/__init__.py'), /from \.payment import PaymentOrder/)
})

test('docker environment example contains placeholders, never usable shared secrets', () => {
  const env = read('apps/server/.env.docker.example')
  assert.match(env, /ENVIRONMENT=production/)
  assert.doesNotMatch(env, /changeme|change-me-to-a-strong-secret/i)
  assert.match(env, /POSTGRES_APP_PASSWORD=/)
  assert.match(env, /REDIS_PASSWORD=/)
  assert.match(env, /JWT_SECRET_KEY=\n/)
  assert.match(env, /POSTGRES_OWNER_PASSWORD=\n/)
  assert.match(env, /POSTGRES_APP_PASSWORD=\n/)
  assert.match(env, /REDIS_PASSWORD=\n/)
})
