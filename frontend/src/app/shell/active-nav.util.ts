/**
 * Which nav item counts as "active" for a given URL. Mirrors the mockup's rule exactly:
 * `active = screen === id || (id === 'home' && (screen === 'goal' || screen === 'capture'))`
 * — Capture and Goal-detail aren't primary destinations of their own, so they highlight Home.
 */
export function activeNavId(url: string): string {
  if (url.startsWith('/capture') || url.startsWith('/goals')) return 'home';
  if (url.startsWith('/garden')) return 'garden';
  if (url.startsWith('/tasks')) return 'tasks';
  if (url.startsWith('/activity')) return 'activity';
  return 'home';
}
