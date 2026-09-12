const INDEXNOW_KEY = '1e4acbc11fe3407a8a641d69a13af696'
const OFFICIAL_PROJECT_ID = 'prj_x4aJCaG3j8QHvwJKOpL3qeE6Lvl3'

export default function handler(request, response) {
  response.setHeader('Cache-Control', 'private, no-store')

  const authorized = process.env.VERCEL === '1'
    && process.env.VERCEL_ENV === 'production'
    && process.env.VERCEL_PROJECT_ID === OFFICIAL_PROJECT_ID
    && typeof request.headers?.host === 'string'
    && request.headers.host === 'zenstory.ai'
    && (request.method === 'GET' || request.method === 'HEAD')

  if (!authorized) {
    response.statusCode = 404
    response.end()
    return
  }

  response.statusCode = 200
  response.setHeader('Content-Type', 'text/plain; charset=utf-8')
  response.setHeader('X-Content-Type-Options', 'nosniff')
  response.end(request.method === 'HEAD' ? undefined : INDEXNOW_KEY)
}
