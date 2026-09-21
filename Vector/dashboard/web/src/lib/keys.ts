/**
 * Keyboard helpers for the dashboard.
 *
 * The motor kill has to be a single, obvious key that works on every page. It
 * lives here so the matching rule can be tested without mounting the app.
 */

export function isTypingTarget(target: unknown): boolean {
  if (target == null || typeof target !== 'object') return false
  const el = target as { tagName?: string; isContentEditable?: boolean }
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable === true
}

/**
 * Plain X, the motor kill. Ignore repeats, and ignore cut/copy chords so Ctrl+X
 * still works in a field.
 */
export function isMotorKillKey(event: KeyboardEvent): boolean {
  if (event.repeat) return false
  if (event.ctrlKey || event.metaKey || event.altKey) return false
  return event.code === 'KeyX' || event.key.toLowerCase() === 'x'
}
