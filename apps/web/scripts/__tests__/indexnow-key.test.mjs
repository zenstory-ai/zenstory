import test from 'node:test'
import assert from 'node:assert/strict'
import indexNowKeyHandler from '../../api/indexnow-key.js'

const INDEXNOW_KEY = '1e4acbc11fe3407a8a641d69a13af696'
const OFFICIAL_PROJECT_ID = 'prj_x4aJCaG3j8QHvwJKOpL3qeE6Lvl3'
const SYSTEM_ENV_KEYS = ['VERCEL', 'VERCEL_ENV', 'VERCEL_PROJECT_ID']
const PRODUCTION_ENV = {
  VERCEL: '1',
  VERCEL_ENV: 'production',
  VERCEL_PROJECT_ID: OFFICIAL_PROJECT_ID,
}

async function invoke({ method = 'GET', headers = {}, env = PRODUCTION_ENV } = {}) {
  const previous = Object.fromEntries(SYSTEM_ENV_KEYS.map((key) => [key, process.env[key]]))
  for (const key of SYSTEM_ENV_KEYS) {
    if (env[key] === undefined) delete process.env[key]
    else process.env[key] = env[key]
  }

  const responseHeaders = new Map()
  let body = Buffer.alloc(0)
  const response = {
    statusCode: 200,
    setHeader(name, value) {
      responseHeaders.set(name.toLowerCase(), String(value))
    },
    status(code) {
      this.statusCode = code
      return this
    },
    end(value) {
      if (value !== undefined) body = Buffer.from(value)
      return this
    },
  }

  try {
    await indexNowKeyHandler({ method, headers }, response)
  } finally {
    for (const key of SYSTEM_ENV_KEYS) {
      if (previous[key] === undefined) delete process.env[key]
      else process.env[key] = previous[key]
    }
  }

  return { status: response.statusCode, headers: responseHeaders, body }
}

function assertRejected(result) {
  assert.equal(result.status, 404)
  assert.equal(result.headers.get('cache-control'), 'private, no-store')
  assert.doesNotMatch(result.body.toString('utf8'), new RegExp(INDEXNOW_KEY))
}

test('serves exact GET bytes and an empty HEAD only on the official production apex', async () => {
  const get = await invoke({ headers: { host: 'zenstory.ai' } })
  assert.equal(get.status, 200)
  assert.equal(get.headers.get('cache-control'), 'private, no-store')
  assert.equal(get.headers.get('content-type'), 'text/plain; charset=utf-8')
  assert.equal(get.headers.get('x-content-type-options'), 'nosniff')
  assert.deepEqual(get.body, Buffer.from(INDEXNOW_KEY, 'ascii'))

  const head = await invoke({ method: 'HEAD', headers: { host: 'zenstory.ai' } })
  assert.equal(head.status, 200)
  assert.equal(head.headers.get('cache-control'), 'private, no-store')
  assert.equal(head.headers.get('content-type'), 'text/plain; charset=utf-8')
  assert.equal(head.headers.get('x-content-type-options'), 'nosniff')
  assert.equal(head.body.length, 0)
})

test('rejects non-apex, malformed and forwarded-host-spoofed requests', async () => {
  const cases = [
    { headers: {} },
    { headers: { host: ['zenstory.ai'] } },
    { headers: { host: 'app.zenstory.ai' } },
    { headers: { host: 'www.zenstory.ai' } },
    { headers: { host: 'geo-preview.zenstory.ai' } },
    { headers: { host: 'app-preview.zenstory.ai' } },
    { headers: { host: 'deployment.vercel.app' } },
    { headers: { host: 'unknown.example' } },
    { headers: { host: 'zenstory.ai:443' } },
    { headers: { host: 'zenstory.ai.' } },
    { headers: { host: 'zenstory.ai.example' } },
    { headers: { 'x-forwarded-host': 'zenstory.ai' } },
    { headers: { host: 'app.zenstory.ai', 'x-forwarded-host': 'zenstory.ai' } },
  ]
  for (const request of cases) assertRejected(await invoke(request))
})

test('rejects wrong deployment identity, non-production, non-Vercel and unsupported methods', async () => {
  const cases = [
    { headers: { host: 'zenstory.ai' }, env: { ...PRODUCTION_ENV, VERCEL_PROJECT_ID: 'prj_wrong' } },
    { headers: { host: 'zenstory.ai' }, env: { ...PRODUCTION_ENV, VERCEL_PROJECT_ID: undefined } },
    { headers: { host: 'zenstory.ai' }, env: { ...PRODUCTION_ENV, VERCEL_ENV: 'preview' } },
    { headers: { host: 'zenstory.ai' }, env: { ...PRODUCTION_ENV, VERCEL_ENV: 'development' } },
    { headers: { host: 'zenstory.ai' }, env: { ...PRODUCTION_ENV, VERCEL: '0' } },
    { headers: { host: 'zenstory.ai' }, env: { ...PRODUCTION_ENV, VERCEL: undefined } },
    { method: 'POST', headers: { host: 'zenstory.ai' } },
    { method: 'OPTIONS', headers: { host: 'zenstory.ai' } },
  ]
  for (const request of cases) assertRejected(await invoke(request))
})
