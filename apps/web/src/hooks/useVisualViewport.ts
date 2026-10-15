import { useEffect } from 'react';

/** Share the keyboard-visible rectangle across fixed shells, dialogs and menus. */
export function useVisualViewport(): void {
  useEffect(() => {
    const viewport = window.visualViewport;
    const style = document.documentElement.style;
    const properties = ['--app-viewport-height', '--app-viewport-top'] as const;
    const previous = properties.map(property => style.getPropertyValue(property));
    let frame: number | null = null;

    const update = () => {
      frame = null;
      // Normalize zoom out of layout sizing and leave zoom panning to the browser.
      // Keep processing resizes: the keyboard can close while still zoomed.
      const scale = viewport?.scale ?? 1;
      style.setProperty('--app-viewport-height', `${viewport ? viewport.height * scale : window.innerHeight}px`);
      style.setProperty('--app-viewport-top', `${scale === 1 ? viewport?.offsetTop ?? 0 : 0}px`);
    };
    const scheduleUpdate = () => {
      if (frame === null) frame = requestAnimationFrame(update);
    };

    update();
    viewport?.addEventListener('resize', scheduleUpdate);
    viewport?.addEventListener('scroll', scheduleUpdate);
    window.addEventListener('resize', scheduleUpdate);
    return () => {
      if (frame !== null) cancelAnimationFrame(frame);
      viewport?.removeEventListener('resize', scheduleUpdate);
      viewport?.removeEventListener('scroll', scheduleUpdate);
      window.removeEventListener('resize', scheduleUpdate);
      properties.forEach((property, index) => {
        if (previous[index]) style.setProperty(property, previous[index]);
        else style.removeProperty(property);
      });
    };
  }, []);
}
