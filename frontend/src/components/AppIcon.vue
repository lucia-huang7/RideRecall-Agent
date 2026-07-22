<template>
  <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path v-for="(path, index) in (paths[name] || paths.chat)" :key="index" :d="path" />
  </svg>
</template>
<script setup>
defineProps({ name: { type: String, default: 'chat' } })
const paths = {
  plus: ['M12 5v14M5 12h14'],
  compose: ['M16 3h5v5M21 3 11 13M19 13v6H5V5h6'],
  panel: ['M3 4h18v16H3zM15 4v16'],
  chat: ['M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8z'],
  book: ['M12 5v16M12 5C9 3 5 3 2 4v16c3-1 7-1 10 1 3-2 7-2 10-1V4c-3-1-7-1-10 1'],
  chart: ['M4 20V10h4v10M10 20V4h4v16M16 20v-7h4v7'],
  settings: ['M9 3h6l.5 3 2 1.2 2.8-1.1 3 5.2-2.3 2v2.4l2.3 2-3 5.2-2.8-1.1-2 1.2-.5 3H9l-.5-3-2-1.2-2.8 1.1-3-5.2 2.3-2v-2.4l-2.3-2 3-5.2 2.8 1.1L8.5 6 9 3', 'M15.5 12a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0'],
  sliders: ['M4 7h7m4 0h5M4 17h3m4 0h9M11 4v6M7 14v6'],
  document: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h6'],
  trash: ['M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7'],
  arrow: ['M12 19V5M5 12l7-7 7 7'],
  northeast: ['M7 17 17 7M7 7h10v10'],
  chevron: ['M9 5l7 7-7 7'],
  close: ['M6 6l12 12M18 6 6 18'],
  refresh: ['M20 7v5h-5M4 17v-5h5M6.1 6.1A8 8 0 0 1 20 12M4 12a8 8 0 0 0 13.9 5.9'],
  check: ['M5 12l4 4L19 6'],
  route: ['M6 8v8M18 8v3a3 3 0 0 1-3 3H9', 'M9 5a3 3 0 1 1-6 0 3 3 0 0 1 6 0M9 19a3 3 0 1 1-6 0 3 3 0 0 1 6 0M21 5a3 3 0 1 1-6 0 3 3 0 0 1 6 0'],
  search: ['M19 19l-5-5M16 9a7 7 0 1 1-14 0 7 7 0 0 1 14 0'],
  upload: ['M12 16V3M7 8l5-5 5 5M4 15v5h16v-5'],
  play: ['m8 4 12 8-12 8z'],
  copy: ['M9 9h12v12H9zM15 5V3H3v12h2'],
  alert: ['M12 3 2 21h20zM12 9v5M12 17v.1'],
  menu: ['M4 6h16M4 12h16M4 18h16']
}
</script>
