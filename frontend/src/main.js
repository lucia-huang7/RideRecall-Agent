import { createApp } from 'vue'
import App from './RideSupport.vue'
import { applyTheme, loadTheme } from './lib/theme.js'
import './ride.css'

applyTheme(loadTheme())
createApp(App).mount('#app')
