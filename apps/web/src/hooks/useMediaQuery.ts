import { useState, useEffect } from "react";

/**
 * Hook to detect media query matches in real-time
 *
 * @param query - CSS media query string to match against
 * @returns Boolean indicating if the media query currently matches
 *
 * @example
 * ```tsx
 * // Basic usage
 * const isWide = useMediaQuery('(min-width: 768px)');
 *
 * // With dark mode preference
 * const prefersDark = useMediaQuery('(prefers-color-scheme: dark)');
 *
 * // With reduced motion preference
 * const prefersReducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)');
 * ```
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);

  useEffect(() => {
    const mediaQuery = window.matchMedia(query);
    const handler = (e: MediaQueryListEvent) => setMatches(e.matches);

    // Listen for changes
    mediaQuery.addEventListener("change", handler);
    return () => mediaQuery.removeEventListener("change", handler);
  }, [query]);

  return matches;
}

/**
 * Hook to detect if current viewport is mobile size (< 768px)
 *
 * Useful for responsive layouts and conditional rendering on mobile devices.
 *
 * @returns Boolean indicating if viewport is mobile-sized (< 768px width)
 *
 * @example
 * ```tsx
 * const isMobile = useIsMobile();
 *
 * return (
 *   <div>
 *     {isMobile ? <MobileNav /> : <DesktopNav />}
 *   </div>
 * );
 * ```
 */
export function useIsMobile(): boolean {
  return !useMediaQuery("(min-width: 768px)");
}

/**
 * Hook to detect if current viewport is tablet size (768px - 1024px)
 *
 * Detects the tablet breakpoint between mobile and desktop sizes.
 *
 * @returns Boolean indicating if viewport is tablet-sized (768px <= width < 1024px)
 *
 * @example
 * ```tsx
 * const isTablet = useIsTablet();
 *
 * return (
 *   <div className={isTablet ? 'tablet-layout' : 'default-layout'}>
 *     Content
 *   </div>
 * );
 * ```
 */
export function useIsTablet(): boolean {
  const isMinTablet = useMediaQuery("(min-width: 768px)");
  const isMaxTablet = !useMediaQuery("(min-width: 1024px)");
  return isMinTablet && isMaxTablet;
}

/**
 * Hook to detect if current viewport is desktop size (>= 1024px)
 *
 * Useful for showing desktop-specific UI elements or layouts.
 *
 * @returns Boolean indicating if viewport is desktop-sized (>= 1024px width)
 *
 * @example
 * ```tsx
 * const isDesktop = useIsDesktop();
 *
 * return (
 *   <div>
 *     {isDesktop && <Sidebar />}
 *     <MainContent />
 *   </div>
 * );
 * ```
 */
export function useIsDesktop(): boolean {
  return useMediaQuery("(min-width: 1024px)");
}
