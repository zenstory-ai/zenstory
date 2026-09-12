import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useLocation as useRouterLocation } from 'react-router-dom'
import { getBoundaryDestination, isManagedOrigin, isSiteRoot, type BoundaryLocation } from '../lib/site-domain'

interface SiteBoundaryProps {
  children: ReactNode
  location?: BoundaryLocation
}

export function SiteBoundary({ children, location = window.location }: SiteBoundaryProps) {
  const routerLocation = useRouterLocation()
  const redirected = useRef(false)
  const [linkRedirectPending, setLinkRedirectPending] = useState(false)
  const currentUrl = new URL(location.href)
  currentUrl.pathname = routerLocation.pathname
  currentUrl.search = routerLocation.search
  currentUrl.hash = routerLocation.hash
  const currentHref = currentUrl.href
  const destination = getBoundaryDestination(currentHref)
  const mountedOnStaticSiteRoot = isSiteRoot(currentHref)
  const shouldReplace = Boolean(destination || mountedOnStaticSiteRoot)

  useEffect(() => {
    if (!shouldReplace || redirected.current) return
    redirected.current = true
    location.replace(destination ?? currentHref)
  }, [currentHref, destination, location, shouldReplace])

  useEffect(() => {
    if (shouldReplace || typeof document === 'undefined' || !isManagedOrigin(currentHref)) return

    const enforceLinkBoundary = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return
      }

      const anchor = (event.target as Element | null)?.closest<HTMLAnchorElement>('a[href]')
      if (!anchor || anchor.target || anchor.hasAttribute('download')) return

      const nextHref = new URL(anchor.href, currentHref).href
      const nextDestination = getBoundaryDestination(nextHref)
      if (!nextDestination && !isSiteRoot(nextHref)) return

      event.preventDefault()
      setLinkRedirectPending(true)
      location.replace(nextDestination ?? nextHref)
    }

    document.addEventListener('click', enforceLinkBoundary, true)
    return () => document.removeEventListener('click', enforceLinkBoundary, true)
  }, [currentHref, location, shouldReplace])

  if (shouldReplace || linkRedirectPending) return null
  return <>{children}</>
}
