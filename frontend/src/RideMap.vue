<template>
  <div class="ride-map" :class="tone" aria-label="Illustrative Chicago route. Not live GPS data." role="img">
    <svg viewBox="0 0 440 220" fill="none" aria-hidden="true">
      <rect width="440" height="220" fill="currentColor" />
      <g stroke="#ffffff" stroke-width="9" opacity=".9"><path d="M0 35H440M0 98H440M0 166H440M52 0V220M124 0V220M203 0V220M279 0V220M350 0V220M421 0V220"/><path d="M-20 230 310-25" stroke-width="14"/></g>
      <path d="M380 0c-30 65 15 100-6 151s-12 50-12 69h78V0Z" fill="#d8d8d8"/>
      <path d="M124 166V98h155V35h71" stroke="#222222" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="124" cy="166" r="9" fill="#ffffff" stroke="#222222" stroke-width="4"/>
      <rect x="343" y="28" width="14" height="14" fill="#222222" stroke="white" stroke-width="3"/>
      <g fill="#7c7c7c" font-size="10" font-family="Arial,sans-serif" letter-spacing="1"><text x="34" y="73">WEST LOOP</text><text x="227" y="139">RIVER NORTH</text><text x="389" y="114" transform="rotate(90 389 114)">LAKE MICHIGAN</text></g>
    </svg>
    <span class="map-pill"><span class="tiny-dot"></span>{{ canceled ? 'Canceled trip' : 'Your trip, connected' }}</span>
    <span class="map-caption">Illustrative route</span>
  </div>
</template>
<script setup>
defineProps({ tone: { type: String, default: 'sand' }, canceled: Boolean })
</script>
