/**
 * Tests for the docs renderer.
 *
 * The link rules exist because the source files are written for two audiences at
 * once: a reader with the repository open, and a reader in the dashboard. A rule
 * that regresses produces a link that quietly 404s, so each of the four link forms
 * that appear in Vector/docs is pinned here.
 */
import { describe, expect, it } from 'vitest'

import { classifyLink, docHref, headingId, outlineOf, renderMarkdown } from './markdown'

describe('headingId', () => {
  it('slugs a heading the way an anchor expects', () => {
    expect(headingId('Tilt to servo angle')).toBe('tilt-to-servo-angle')
  })

  it('keeps the leading section number, which the docs rely on', () => {
    expect(headingId('1. Tilt to servo angle')).toBe('1-tilt-to-servo-angle')
  })

  it('collapses punctuation rather than leaving it in the id', () => {
    expect(headingId('The linear map, and what it costs')).toBe('the-linear-map-and-what-it-costs')
    expect(headingId('Why gimbals cannot control attitude')).toBe(
      'why-gimbals-cannot-control-attitude',
    )
  })

  it('does not leave a trailing dash on a heading that ends in punctuation', () => {
    expect(headingId('Is it level?')).toBe('is-it-level')
  })
})

describe('classifyLink', () => {
  it('treats a sibling document as a document', () => {
    expect(classifyLink('03-kinematics.md')).toEqual({
      kind: 'doc',
      slug: '03-kinematics',
      anchor: undefined,
    })
  })

  it('carries the anchor of a cross-document link', () => {
    expect(classifyLink('03-kinematics.md#5-the-levelling-law')).toEqual({
      kind: 'doc',
      slug: '03-kinematics',
      anchor: '5-the-levelling-law',
    })
  })

  it('accepts the explicit ./ form', () => {
    expect(classifyLink('./09-safety.md')).toMatchObject({ kind: 'doc', slug: '09-safety' })
  })

  it('treats a repository path as a path, not a document', () => {
    expect(classifyLink('../dashboard/server/kinematics.py')).toEqual({
      kind: 'path',
      path: '../dashboard/server/kinematics.py',
    })
  })

  it('does not mistake a markdown file outside docs for a sibling', () => {
    // No two-digit prefix, so it is not one of the numbered documents.
    expect(classifyLink('../../README.md')).toMatchObject({ kind: 'path' })
  })

  it('recognises a same-page anchor', () => {
    expect(classifyLink('#headroom')).toEqual({ kind: 'anchor', id: 'headroom' })
  })

  it('recognises external links', () => {
    expect(classifyLink('https://ardupilot.org')).toMatchObject({ kind: 'external' })
    expect(classifyLink('mailto:someone@example.com')).toMatchObject({ kind: 'external' })
  })
})

describe('docHref', () => {
  it('routes to the document', () => {
    expect(docHref('04-control')).toBe('#/docs/04-control')
  })

  it('puts the anchor in the query, because the fragment is spent on the route', () => {
    expect(docHref('04-control', 'output-arbiter')).toBe('#/docs/04-control?h=output-arbiter')
  })
})

describe('renderMarkdown', () => {
  it('gives every heading an id so anchors resolve', () => {
    const html = renderMarkdown('## Reachable workspace\n\n### Headroom\n')
    expect(html).toContain('id="reachable-workspace"')
    // Third-level too: the docs link to those even though the outline omits them.
    expect(html).toContain('id="headroom"')
  })

  it('rewrites a cross-document link to an in-app route', () => {
    const html = renderMarkdown('See [the kinematics](03-kinematics.md#1-tilt-to-servo-angle).')
    expect(html).toContain('href="#/docs/03-kinematics?h=1-tilt-to-servo-angle"')
    expect(html).not.toContain('.md')
  })

  it('renders a repository path as a path rather than a dead link', () => {
    const html = renderMarkdown('See [kinematics.py](../dashboard/server/kinematics.py).')
    expect(html).toContain('<code class="path"')
    expect(html).toContain('kinematics.py')
    expect(html).not.toContain('<a')
  })

  it('marks a same-page anchor for the click handler', () => {
    const html = renderMarkdown('See [headroom](#headroom).')
    expect(html).toContain('data-anchor="headroom"')
  })

  it('opens external links in a new tab without leaking the referrer', () => {
    const html = renderMarkdown('[ArduPilot](https://ardupilot.org)')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener"')
  })

  it('escapes a quote in a title so the attribute cannot break out', () => {
    const html = renderMarkdown('[x](https://example.com "say \\"hi\\"")')
    expect(html).toContain('&quot;hi&quot;')
    expect(html).not.toMatch(/title="[^"]*"hi"/)
  })

  it('titles a repository path with the path itself', () => {
    // The hover text is the one place the full path is still visible, so it wins
    // over any title the author wrote.
    const html = renderMarkdown('[kinematics](../dashboard/server/kinematics.py "ignored")')
    expect(html).toContain('title="../dashboard/server/kinematics.py"')
    expect(html).not.toContain('ignored')
  })

  it('still renders ordinary markdown', () => {
    const html = renderMarkdown('A **bold** word and `code`.\n\n| a | b |\n| - | - |\n| 1 | 2 |\n')
    expect(html).toContain('<strong>bold</strong>')
    expect(html).toContain('<code>code</code>')
    expect(html).toContain('<table>')
  })
})

describe('outlineOf', () => {
  it('lists second-level headings only', () => {
    const outline = outlineOf('# Title\n\n## One\n\ntext\n\n### Deep\n\n## Two\n')
    expect(outline).toEqual([
      { text: 'One', id: 'one' },
      { text: 'Two', id: 'two' },
    ])
  })

  it('ignores a hash inside a fenced block', () => {
    // A shell comment in a code fence is not a heading, and the docs are full of them.
    const outline = outlineOf('## Real\n\n```sh\n## not a heading\n```\n\n## Also real\n')
    expect(outline.map((entry) => entry.text)).toEqual(['Real', 'Also real'])
  })

  it('does not let a nested fence end the block early', () => {
    const outline = outlineOf('~~~\n```\n## hidden\n```\n~~~\n\n## Real\n')
    expect(outline.map((entry) => entry.text)).toEqual(['Real'])
  })
})
