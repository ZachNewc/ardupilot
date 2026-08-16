/**
 * Markdown rendering for the docs viewer.
 *
 * The files in Vector/docs are written to read correctly in an editor and on a
 * repository page: they link to sibling `.md` files and to source files by relative
 * path. Neither form survives being rendered in the dashboard, which is a single
 * page behind a hash route and does not publish the repository. Rather than
 * compromise how the files read at rest, the link forms are translated here.
 *
 * This lives outside the component so the rules can be tested directly -- see
 * markdown.test.ts. Getting them wrong produces links that silently 404, which is
 * the kind of fault nobody notices until a reader hits it.
 */
import { Marked } from 'marked'

/** Slug rule for heading anchors. Shared with the in-page outline so the two agree. */
export const headingId = (text: string): string =>
  text
    .toLowerCase()
    .replace(/[^\w]+/g, '-')
    .replace(/(^-|-$)/g, '')

const escapeHtml = (value: string): string =>
  value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** A sibling document, `04-control.md` or `04-control.md#section`. */
const SIBLING_DOC = /^(?:\.\/)?(\d{2}-[\w-]+)\.md(?:#(.+))?$/

/**
 * Where a documentation link should point in the app.
 *
 * Exported for the tests, and because the shape of the decision is the interesting
 * part: everything else here is string assembly.
 */
export type LinkTarget =
  | { kind: 'external'; href: string }
  | { kind: 'anchor'; id: string }
  | { kind: 'doc'; slug: string; anchor?: string }
  | { kind: 'path'; path: string }

export function classifyLink(href: string): LinkTarget {
  if (/^(?:https?:|mailto:)/.test(href)) return { kind: 'external', href }
  if (href.startsWith('#')) return { kind: 'anchor', id: href.slice(1) }

  const doc = href.match(SIBLING_DOC)
  if (doc) return { kind: 'doc', slug: doc[1], anchor: doc[2] }

  return { kind: 'path', path: href }
}

/**
 * Route for a cross-document link.
 *
 * The anchor rides in the query rather than a second `#`, because hash history has
 * already spent the fragment on the route itself.
 */
export function docHref(slug: string, anchor?: string): string {
  return `#/docs/${slug}${anchor ? `?h=${encodeURIComponent(anchor)}` : ''}`
}

/**
 * A configured renderer, kept private to this module.
 *
 * A local instance rather than the global `marked`, so importing this file cannot
 * change how markdown renders anywhere else.
 */
const renderer = new Marked({
  renderer: {
    heading({ tokens, depth }) {
      const text = this.parser.parseInline(tokens)
      const plain = tokens.map((token) => ('text' in token ? token.text : '')).join('')
      return `<h${depth} id="${headingId(plain)}">${text}</h${depth}>\n`
    },

    link({ href, title, tokens }) {
      const text = this.parser.parseInline(tokens)
      const attrs = title ? ` title="${escapeHtml(title)}"` : ''
      const target = classifyLink(href)

      switch (target.kind) {
        case 'external':
          return `<a href="${escapeHtml(target.href)}"${attrs} target="_blank" rel="noopener">${text}</a>`

        // Handled by a delegated click listener: writing to the hash would replace
        // the route instead of scrolling.
        case 'anchor':
          return `<a href="#" data-anchor="${escapeHtml(target.id)}"${attrs}>${text}</a>`

        case 'doc':
          return `<a href="${docHref(target.slug, target.anchor)}"${attrs}>${text}</a>`

        // A path into the repository. There is nothing to link to, so show the path
        // rather than a link that would 404.
        case 'path':
          return `<code class="path" title="${escapeHtml(target.path)}">${text}</code>`
      }
    },
  },
})

export function renderMarkdown(source: string): string {
  return renderer.parse(source, { async: false })
}

/**
 * Second-level headings, which is what the in-page outline lists.
 *
 * Fenced blocks are skipped: the docs are full of shell snippets, and a `##` comment
 * inside one is a comment, not a heading.
 */
export function outlineOf(source: string): { text: string; id: string }[] {
  const found: { text: string; id: string }[] = []
  let fence: string | null = null

  for (const line of source.split('\n')) {
    const delimiter = line.match(/^\s*(```+|~~~+)/)
    if (delimiter) {
      // A fence closes only on its own kind, so a ``` inside a ~~~ block is content.
      if (fence === null) fence = delimiter[1][0]
      else if (delimiter[1][0] === fence) fence = null
      continue
    }
    if (fence !== null) continue

    if (/^##\s+/.test(line)) {
      const text = line.replace(/^##\s+/, '').trim()
      found.push({ text, id: headingId(text) })
    }
  }
  return found
}
