/**
 * Tests for detaching store data.
 *
 * These exist because of a real failure: the Setup page cloned the config document
 * with `structuredClone`, which throws on a Vue reactive proxy. The whole editor
 * silently rendered empty -- no error banner, no missing panel, just no fields. The
 * tests below use a genuine `reactive()` proxy so the same mistake cannot come back
 * disguised as a passing unit test on a plain object.
 */
import { reactive } from 'vue'
import { describe, expect, it } from 'vitest'

import { detached } from './store'

describe('detached', () => {
  it('copies a reactive proxy, which is the case that broke', () => {
    const source = reactive({ arms: [{ id: 'north', outer: { channel: 1 } }] })
    const copy = detached(source.arms)
    expect(copy).toEqual([{ id: 'north', outer: { channel: 1 } }])
  })

  it('is needed because structuredClone cannot do this', () => {
    // Pinning the reason, so nobody "simplifies" the helper away.
    const source = reactive({ a: 1 })
    expect(() => structuredClone(source)).toThrow()
    expect(() => detached(source)).not.toThrow()
  })

  it('detaches deeply, so editing the copy cannot write back to the store', () => {
    const source = reactive({ gimbal_defaults: { outer: { center_us: 1500 } } })
    const copy = detached(source)
    copy.gimbal_defaults.outer.center_us = 1600
    expect(source.gimbal_defaults.outer.center_us).toBe(1500)
  })

  it('detaches array entries too, which is where the arms live', () => {
    const source = reactive({ arms: [{ id: 'north', motors: { top: { channel: 4 } } }] })
    const copy = detached(source)
    copy.arms[0].motors.top.channel = 9
    copy.arms.push({ id: 'east', motors: { top: { channel: 6 } } })
    expect(source.arms[0].motors.top.channel).toBe(4)
    expect(source.arms).toHaveLength(1)
  })

  it('returns null rather than throwing when there is nothing yet', () => {
    // The config is null until the first socket frame, and the page renders before then.
    expect(detached(undefined)).toBeNull()
    expect(detached(null)).toBeNull()
  })

  it('leaves a plain object alone', () => {
    const source = { link: { device: '/dev/ttyACM0', baud: 115200 } }
    expect(detached(source)).toEqual(source)
    expect(detached(source)).not.toBe(source)
  })
})
