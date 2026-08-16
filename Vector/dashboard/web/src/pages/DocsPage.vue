<!--
  Docs: renders the markdown in Vector/docs straight from the repository.

  There is no second copy of the documentation. The server reads the same files a
  reader would open in an editor or on the repository page, so the docs cannot go
  stale relative to what the dashboard shows.
-->
<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PanelCard from '../components/PanelCard.vue'
import { outlineOf, renderMarkdown } from '../lib/markdown'

interface DocEntry {
  slug: string
  file: string
  title: string
  body: string
}

const route = useRoute()
const router = useRouter()

const docs = ref<DocEntry[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    const response = await fetch('/api/docs')
    if (!response.ok) throw new Error(`server returned ${response.status}`)
    docs.value = (await response.json()) as DocEntry[]
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : String(exception)
  } finally {
    loading.value = false
  }
})

const current = computed(() => {
  const slug = route.params.slug as string | undefined
  return docs.value.find((doc) => doc.slug === slug) ?? docs.value[0] ?? null
})

const rendered = computed(() => (current.value ? renderMarkdown(current.value.body) : ''))

/** Headings become an in-page outline, which is what makes a long doc navigable. */
const outline = computed(() => (current.value ? outlineOf(current.value.body) : []))

watch(
  () => docs.value.length,
  (count) => {
    // Land on the first document rather than an empty pane.
    if (count && !route.params.slug) router.replace(`/docs/${docs.value[0].slug}`)
  },
)

const reader = ref<HTMLElement | null>(null)

/** Scroll a heading into view once the rendered markdown is actually in the DOM. */
const scrollToHeading = async (id: string) => {
  await nextTick()
  reader.value?.querySelector(`#${CSS.escape(id)}`)?.scrollIntoView({ behavior: 'smooth' })
}

const goToHeading = (id: string) => {
  // Recorded in the query so the position survives a reload or a shared link. Hash
  // history has already spent the fragment on the route, hence a query rather than
  // a second '#'.
  router.replace({ params: route.params, query: { ...route.query, h: id } })
  scrollToHeading(id)
}

/** Anchors inside v-html need delegation; there is no component to bind to. */
const onReaderClick = (event: MouseEvent) => {
  const anchor = (event.target as HTMLElement).closest('a[data-anchor]')
  if (!anchor) return
  event.preventDefault()
  goToHeading(anchor.getAttribute('data-anchor') as string)
}

// An anchor arriving on the URL, whether from a cross-document link or a reload.
watch(
  [() => route.query.h, () => current.value?.slug],
  ([target, slug]) => {
    if (!slug) return
    if (typeof target === 'string' && target) scrollToHeading(target)
    else reader.value?.scrollIntoView({ block: 'start' })
  },
  { immediate: true },
)
</script>

<template>
  <div class="docs">
    <aside class="index">
      <div class="label pad">Contents</div>
      <RouterLink
        v-for="doc in docs"
        :key="doc.slug"
        :to="`/docs/${doc.slug}`"
        class="entry"
        :class="{ on: current?.slug === doc.slug }"
      >
        <span class="num mono">{{ doc.slug.split('-')[0] }}</span>
        <span>{{ doc.title }}</span>
      </RouterLink>

      <template v-if="outline.length">
        <div class="label pad top">On this page</div>
        <button
          v-for="item in outline"
          :key="item.id"
          class="sub"
          :class="{ on: route.query.h === item.id }"
          type="button"
          @click="goToHeading(item.id)"
        >
          {{ item.text }}
        </button>
      </template>
    </aside>

    <div ref="reader" class="reader" @click="onReaderClick">
      <p v-if="loading" class="faint">Loading documentation&hellip;</p>

      <PanelCard v-else-if="error" title="Documentation unavailable">
        <p class="mono small">{{ error }}</p>
        <p class="faint small">
          The files live in <code>Vector/docs/</code> and read fine in an editor; only this
          in-app view needs the server.
        </p>
      </PanelCard>

      <PanelCard v-else-if="!docs.length" title="No documents found">
        <p class="faint small">
          Nothing in <code>Vector/docs/</code> yet.
        </p>
      </PanelCard>

      <article v-else class="markdown" v-html="rendered" />

      <p v-if="current" class="faint small source">
        Source: <code>Vector/docs/{{ current.file }}</code>
      </p>
    </div>
  </div>
</template>

<style scoped>
.docs {
  display: grid;
  grid-template-columns: 232px minmax(0, 1fr);
  gap: var(--s5);
  align-items: start;
}

.index {
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
  border-right: 1px solid var(--line);
  padding-right: var(--s3);
}

.pad {
  padding: var(--s2);
}

.top {
  margin-top: var(--s4);
  border-top: 1px solid var(--line);
  padding-top: var(--s3);
}

.entry {
  display: flex;
  gap: var(--s2);
  padding: 6px var(--s2);
  border-radius: var(--radius-sm);
  color: var(--text-dim);
  font-size: var(--fs-sm);
  text-decoration: none;
  border-left: 2px solid transparent;
}

.entry:hover {
  background: var(--surface-2);
  color: var(--text);
  text-decoration: none;
}

.entry.on {
  background: var(--accent-wash);
  color: var(--text);
  border-left-color: var(--accent);
}

.num {
  color: var(--text-faint);
  font-size: var(--fs-xs);
  padding-top: 1px;
}

.sub {
  padding: 3px var(--s2) 3px var(--s4);
  font-size: var(--fs-xs);
  color: var(--text-faint);
  text-align: left;
  background: none;
  border: none;
  cursor: pointer;
  font-family: inherit;
}

.sub:hover {
  color: var(--accent);
}

.sub.on {
  color: var(--accent);
}

.reader {
  min-width: 0;
}

.source {
  margin-top: var(--s5);
  padding-top: var(--s3);
  border-top: 1px solid var(--line);
}

.small {
  font-size: var(--fs-xs);
}

@media (max-width: 900px) {
  .docs {
    grid-template-columns: 1fr;
  }

  .index {
    position: static;
    border-right: none;
    border-bottom: 1px solid var(--line);
    padding: 0 0 var(--s3);
  }
}
</style>

<!--
  Markdown output is not scoped: v-html content carries no scope attribute, so these
  rules have to be global. They are namespaced under .markdown to stay contained.
-->
<style>
.markdown {
  max-width: 52rem;
  color: var(--text-dim);
  font-size: var(--fs-lg);
  line-height: 1.72;
}

.markdown h1 {
  font-size: 1.7rem;
  color: var(--text);
  margin: 0 0 var(--s4);
  letter-spacing: -0.01em;
}

.markdown h2 {
  font-size: 1.2rem;
  color: var(--text);
  margin: var(--s6) 0 var(--s3);
  padding-bottom: var(--s2);
  border-bottom: 1px solid var(--line);
}

.markdown h3 {
  font-size: 1rem;
  color: var(--text);
  margin: var(--s5) 0 var(--s2);
}

.markdown h4 {
  font-size: var(--fs-md);
  color: var(--text);
  margin: var(--s4) 0 var(--s2);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.markdown p {
  margin: 0 0 var(--s3);
}

.markdown ul,
.markdown ol {
  margin: 0 0 var(--s3);
  padding-left: var(--s5);
}

.markdown li {
  margin-bottom: var(--s1);
}

.markdown li > ul,
.markdown li > ol {
  margin-top: var(--s1);
}

.markdown strong {
  color: var(--text);
  font-weight: 600;
}

.markdown code {
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 0.86em;
  color: var(--text);
}

.markdown pre {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: var(--s3) var(--s4);
  overflow-x: auto;
  margin: 0 0 var(--s4);
  font-size: var(--fs-sm);
  line-height: 1.6;
}

.markdown pre code {
  background: none;
  border: none;
  padding: 0;
  font-size: inherit;
  color: var(--text-dim);
}

.markdown table {
  width: 100%;
  border-collapse: collapse;
  margin: 0 0 var(--s4);
  font-size: var(--fs-md);
}

.markdown th,
.markdown td {
  text-align: left;
  padding: var(--s2) var(--s3);
  border-bottom: 1px solid var(--line);
  vertical-align: top;
}

.markdown th {
  color: var(--text);
  font-size: var(--fs-xs);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  background: var(--surface-2);
}

.markdown blockquote {
  margin: 0 0 var(--s4);
  padding: var(--s2) var(--s4);
  border-left: 3px solid var(--accent);
  background: var(--accent-wash);
  color: var(--text-dim);
}

.markdown blockquote p:last-child {
  margin-bottom: 0;
}

.markdown hr {
  border: none;
  border-top: 1px solid var(--line);
  margin: var(--s6) 0;
}

.markdown img {
  max-width: 100%;
  border-radius: var(--radius-sm);
}

/* A path into the repository, shown in place of a link the browser cannot follow. */
.markdown code.path {
  color: var(--text-dim);
  border-style: dashed;
}
</style>
