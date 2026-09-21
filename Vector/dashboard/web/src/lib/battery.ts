/**
 * Pack remaining from voltage for the 6S2P li-ion pack.
 *
 * Parallel cells do not change the voltage. This is an open-circuit-ish NMC/NCA
 * curve (4.2 V full). Under load the reading sags, which is the honest error
 * for a bench with no coulomb counter.
 */

export const PACK_CELLS_SERIES = 6

/** Cell volts → remaining fraction, high to low. */
const LI_ION_CELL_OCV: ReadonlyArray<readonly [number, number]> = [
  [4.2, 1.0],
  [4.15, 0.95],
  [4.1, 0.9],
  [4.05, 0.84],
  [4.0, 0.77],
  [3.95, 0.69],
  [3.9, 0.6],
  [3.85, 0.52],
  [3.8, 0.44],
  [3.75, 0.37],
  [3.7, 0.3],
  [3.65, 0.24],
  [3.6, 0.19],
  [3.55, 0.15],
  [3.5, 0.11],
  [3.45, 0.08],
  [3.4, 0.06],
  [3.3, 0.03],
  [3.2, 0.01],
  [3.0, 0.0],
]

export function packSocPercent(packVolts: number, cellsSeries = PACK_CELLS_SERIES): number | null {
  if (!(packVolts > 0) || cellsSeries < 1) {
    return null
  }
  const cell = packVolts / cellsSeries
  if (cell >= LI_ION_CELL_OCV[0][0]) {
    return 100
  }
  const last = LI_ION_CELL_OCV[LI_ION_CELL_OCV.length - 1]
  if (cell <= last[0]) {
    return 0
  }
  for (let i = 0; i < LI_ION_CELL_OCV.length - 1; i += 1) {
    const [hiV, hiSoc] = LI_ION_CELL_OCV[i]
    const [loV, loSoc] = LI_ION_CELL_OCV[i + 1]
    if (cell <= hiV && cell >= loV) {
      const t = (cell - loV) / (hiV - loV)
      return (loSoc + t * (hiSoc - loSoc)) * 100
    }
  }
  return 0
}

export function batteryTone(
  packVolts: number,
  cellsSeries = PACK_CELLS_SERIES,
): 'ok' | 'warn' | 'danger' | 'dim' {
  if (!(packVolts > 0)) {
    return 'dim'
  }
  const cell = packVolts / cellsSeries
  if (cell < 3.2) {
    return 'danger'
  }
  if (cell < 3.4) {
    return 'warn'
  }
  return 'ok'
}
