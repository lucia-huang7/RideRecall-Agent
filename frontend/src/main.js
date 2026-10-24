import { router } from "./lib/router.js";
import { createApp } from "vue";
import App from "./App.vue";
import { applyTheme, loadTheme } from "./lib/theme.js";
import "./ride.css";

applyTheme(loadTheme());
createApp(App).use(router).mount("#app");
