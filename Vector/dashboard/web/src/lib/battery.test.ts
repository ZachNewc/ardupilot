import { describe, expect, it } from 'vitest'

import { PACK_CELLS_SERIES, batteryTone, packSocPercent } from './battery'

describe('packSocPercent', () => {
  it('is full at 4.2 V per cell', () => {
    expect(packSocPercent(4.2 * PACK_CELLS_SERIES)).toBe(100)
  })

  it('is empty at 3.0 V per cell', () => {
    expect(packSocPercent(3.0 * PACK_CELLS_SERIES)).toBe(0)
  })

  it('follows the li-ion mid curve, not a linear 3.0–4.2 ramp', () => {
    const mid = packSocPercent(3.7 * PACK_CELLS_SERIES)
    expect(mid).toBeCloseTo(30, 5)
    // A linear 3.0–4.2 map would call 3.7 V about 58%.
    expect(mid).toBeLessThan(40)
  })

  it('interpolates between tabulated points', () => {
    const value = packSocPercent(3.725 * PACK_CELLS_SERIES)
    expect(value).toBeCloseTo(33.5, 5)
  })

  it('returns null when there is no pack voltage', () => {
    expect(packSocPercent(0)).toBeNull()
    expect(packSocPercent(-1)).toBeNull()
  })
})

describe('batteryTone', () => {
  it('matches the 6s floors already used on Overview', () => {
    expect(batteryTone(25.2)).toBe('ok')
    expect(batteryTone(20.3)).toBe('warn')
    expect(batteryTone(19.1)).toBe('danger')
    expect(batteryTone(0)).toBe('dim')
  })
})
