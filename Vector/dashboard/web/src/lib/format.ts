/** Small formatting helpers, so units and precision are consistent everywhere. */

export const num = (value: number | null | undefined, digits = 1): string =>
  value === null || value === undefined || Number.isNaN(value) ? '--' : value.toFixed(digits)

export const deg = (value: number | null | undefined, digits = 1): string =>
  value === null || value === undefined ? '--' : `${value.toFixed(digits)}\u00b0`

export const signedDeg = (value: number | null | undefined, digits = 1): string => {
  if (value === null || value === undefined) return '--'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}\u00b0`
}

export const us = (value: number | null | undefined): string =>
  value === null || value === undefined ? '--' : `${Math.round(value)} \u00b5s`

export const pct = (value: number | null | undefined, digits = 0): string =>
  value === null || value === undefined ? '--' : `${(value * 100).toFixed(digits)}%`

export const volts = (value: number | null | undefined): string =>
  !value ? '--' : `${value.toFixed(2)} V`

export const amps = (value: number | null | undefined): string =>
  value === null || value === undefined ? '--' : `${value.toFixed(1)} A`

/** Ages are the most common "is this data real" signal, so they get their own format. */
export function age(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return 'never'
  if (seconds < 1) return 'now'
  if (seconds < 60) return `${seconds.toFixed(0)}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}

export const clockTime = (epochSeconds: number): string =>
  new Date(epochSeconds * 1000).toLocaleTimeString([], { hour12: false })

/** Per-arm colour, resolved from the CSS custom properties in tokens.css. */
export function armColor(armId: string, index = 0): string {
  const known = ['north', 'east', 'south', 'west']
  const slot = known.includes(armId) ? armId : known[index % known.length]
  return `var(--arm-${slot})`
}
