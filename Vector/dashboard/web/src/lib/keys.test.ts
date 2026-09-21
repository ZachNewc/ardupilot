import { describe, expect, it } from 'vitest'

import { isMotorKillKey, isTypingTarget } from './keys'

function key(partial: Partial<KeyboardEvent>): KeyboardEvent {
  return {
    repeat: false,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    code: '',
    key: '',
    ...partial,
  } as KeyboardEvent
}

describe('isMotorKillKey', () => {
  it('fires on a plain X', () => {
    expect(isMotorKillKey(key({ code: 'KeyX', key: 'x' }))).toBe(true)
    expect(isMotorKillKey(key({ code: 'KeyX', key: 'X' }))).toBe(true)
  })

  it('ignores other letters', () => {
    expect(isMotorKillKey(key({ code: 'KeyS', key: 's' }))).toBe(false)
  })

  it('ignores key repeat so holding X does not flood the link', () => {
    expect(isMotorKillKey(key({ code: 'KeyX', key: 'x', repeat: true }))).toBe(false)
  })

  it('leaves Ctrl+X and Cmd+X alone so cut still works', () => {
    expect(isMotorKillKey(key({ code: 'KeyX', key: 'x', ctrlKey: true }))).toBe(false)
    expect(isMotorKillKey(key({ code: 'KeyX', key: 'x', metaKey: true }))).toBe(false)
    expect(isMotorKillKey(key({ code: 'KeyX', key: 'x', altKey: true }))).toBe(false)
  })
})

describe('isTypingTarget', () => {
  it('treats form fields as typing targets', () => {
    expect(isTypingTarget({ tagName: 'INPUT' })).toBe(true)
    expect(isTypingTarget({ tagName: 'TEXTAREA' })).toBe(true)
    expect(isTypingTarget({ tagName: 'SELECT' })).toBe(true)
    expect(isTypingTarget({ tagName: 'BUTTON' })).toBe(false)
    expect(isTypingTarget({ isContentEditable: true })).toBe(true)
    expect(isTypingTarget(null)).toBe(false)
  })
})
