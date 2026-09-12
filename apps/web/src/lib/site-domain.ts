import siteRouting from '../../content/site-routing.json'

type SitePair = {
  siteOrigin: string
  appOrigin: string
}

export type BoundaryLocation = Pick<Location, 'href' | 'replace'>

const productionPair: SitePair = {
  siteOrigin: siteRouting.siteOrigin,
  appOrigin: siteRouting.appOrigin,
}

const previewPair: SitePair = {
  siteOrigin: siteRouting.previewSiteOrigin,
  appOrigin: siteRouting.previewAppOrigin,
}

const pairs = [productionPair, previewPair] as const

function matchesPrefix(pathname: string, prefixes: readonly string[]): boolean {
  return prefixes.some((prefix) => pathname === `/${prefix}` || pathname.startsWith(`/${prefix}/`))
}

export function isAppPath(pathname: string): boolean {
  return matchesPrefix(pathname, siteRouting.appPrefixes)
}

function pairForOrigin(origin: string): SitePair | undefined {
  return pairs.find((pair) => pair.siteOrigin === origin || pair.appOrigin === origin)
}

export function isManagedOrigin(href: string): boolean {
  return Boolean(pairForOrigin(new URL(href).origin))
}

function withOrigin(url: URL, origin: string): string {
  return `${origin}${url.pathname}${url.search}${url.hash}`
}

export function isSiteRoot(href: string): boolean {
  const url = new URL(href)
  const pair = pairForOrigin(url.origin)
  return Boolean(pair && url.origin === pair.siteOrigin && url.pathname === '/')
}

export function getBoundaryDestination(href: string): string | null {
  const url = new URL(href)
  const pair = pairForOrigin(url.origin)
  if (!pair) return null

  if (url.origin === pair.siteOrigin && isAppPath(url.pathname)) {
    return withOrigin(url, pair.appOrigin)
  }

  if (url.origin === pair.appOrigin && matchesPrefix(url.pathname, siteRouting.sitePrefixes)) {
    return withOrigin(url, pair.siteOrigin)
  }

  return null
}

export function getPublicCanonicalUrl(href: string): string {
  const url = new URL(href)
  const pair = pairForOrigin(url.origin)

  if (pair) {
    if (isAppPath(url.pathname)) {
      return `${productionPair.appOrigin}${url.pathname}`
    }
    if (matchesPrefix(url.pathname, siteRouting.sitePrefixes)) {
      return `${productionPair.siteOrigin}${url.pathname}`
    }
    return `${url.origin === pair.appOrigin ? productionPair.appOrigin : productionPair.siteOrigin}${url.pathname}`
  }

  return `${url.origin}${url.pathname}`
}
