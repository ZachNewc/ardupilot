/**
 * Routes, and the navigation metadata the sidebar is built from.
 *
 * Adding a page means adding one entry here; `AppNav` renders whatever is in this
 * list, so nothing has to be kept in sync by hand.
 */

import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'

export interface NavMeta {
  title: string
  /** One line describing the page, shown under the heading. */
  blurb: string
  icon: string
  group: 'Fly' | 'Hardware' | 'Reference'
}

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'overview',
    component: () => import('./pages/OverviewPage.vue'),
    meta: {
      title: 'Overview',
      blurb: 'Link, attitude, and every arm at a glance.',
      icon: 'grid',
      group: 'Fly',
    } satisfies NavMeta,
  },
  {
    path: '/arms',
    name: 'arms',
    component: () => import('./pages/ArmsPage.vue'),
    meta: {
      title: 'Arms',
      blurb: 'Aim each gimbal, inspect its envelope, and test its motors.',
      icon: 'arms',
      group: 'Fly',
    } satisfies NavMeta,
  },
  {
    path: '/stabilize',
    name: 'stabilize',
    component: () => import('./pages/StabilizePage.vue'),
    meta: {
      title: 'Stabilize',
      blurb: 'Bench levelling demo: hold thrust vertical while the frame is moved.',
      icon: 'level',
      group: 'Fly',
    } satisfies NavMeta,
  },
  {
    path: '/telemetry',
    name: 'telemetry',
    component: () => import('./pages/TelemetryPage.vue'),
    meta: {
      title: 'Telemetry',
      blurb: 'Attitude, rates, battery and per-ESC data as it arrives.',
      icon: 'wave',
      group: 'Fly',
    } satisfies NavMeta,
  },
  {
    path: '/world',
    name: 'world',
    component: () => import('./pages/WorldPage.vue'),
    meta: {
      title: '3D World',
      blurb: 'Scene scaffold for the simulated vehicle. Not yet wired to telemetry.',
      icon: 'cube',
      group: 'Hardware',
    } satisfies NavMeta,
  },
  {
    path: '/setup',
    name: 'setup',
    component: () => import('./pages/SetupPage.vue'),
    meta: {
      title: 'Setup',
      blurb: 'Channel map, servo calibration, limits and per-arm geometry.',
      icon: 'sliders',
      group: 'Hardware',
    } satisfies NavMeta,
  },
  {
    path: '/docs/:slug?',
    name: 'docs',
    component: () => import('./pages/DocsPage.vue'),
    meta: {
      title: 'Docs',
      blurb: 'The project documentation, served straight from the repository.',
      icon: 'book',
      group: 'Reference',
    } satisfies NavMeta,
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  // Hash history keeps the server free of catch-all routing: it only ever serves
  // one HTML file plus the API.
  history: createWebHashHistory(),
  routes,
})

export const navRoutes = routes.filter((route) => route.meta) as (RouteRecordRaw & {
  meta: NavMeta
  path: string
  name: string
})[]

export default router
