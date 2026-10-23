<template>
  <section class="page-heading">
    <p class="eyebrow">YOUR SPACE</p>
    <h1>{{ identity.subject || "Jamie" }}</h1>
    <p>{{ isDemo ? "Local demo rider" : identity.roles.join(" · ") }}</p>
  </section>
  <section class="content-block top-zero">
    <div class="profile-card">
      <span class="large-avatar">{{
        identity.subject?.[0]?.toUpperCase() || "J"
      }}</span>
      <div>
        <h2>Everything in one place.</h2>
        <p>Your case history follows your workspace account.</p>
      </div>
    </div>
    <AgentModePicker v-model="agentMode" :disabled="busy" />
    <section class="appearance-card" aria-labelledby="appearance-title">
      <div class="appearance-heading">
        <div>
          <h2 id="appearance-title">Appearance</h2>
          <p>Same clarity. A different expression.</p>
        </div>
        <span class="theme-swatch" aria-hidden="true"></span>
      </div>
      <div class="theme-options" role="group" aria-label="Color theme">
        <button
          :aria-pressed="theme === 'mono'"
          :class="{ selected: theme === 'mono' }"
          @click="setTheme('mono')"
        >
          <span class="mono-preview" aria-hidden="true"></span>Black &amp;
          white<Icon v-if="theme === 'mono'" name="check" /></button
        ><button
          :aria-pressed="theme === 'rainbow'"
          :class="{ selected: theme === 'rainbow' }"
          @click="setTheme('rainbow')"
        >
          <span class="rainbow-preview" aria-hidden="true"></span>Rainbow
          Mode<Icon v-if="theme === 'rainbow'" name="check" />
        </button>
      </div>
    </section>
    <button v-if="canRider" class="settings-row" @click="go('/help')">
      <Icon name="chat" /><span>My support cases</span
      ><Icon name="chevron" /></button
    ><button v-if="canSpecialist" class="settings-row" @click="go('/agent')">
      <Icon name="panel" /><span>Open simulated Agent desk</span
      ><Icon name="chevron" /></button
    ><button v-if="canOperator" class="settings-row" @click="go('/manage')">
      <Icon name="sliders" /><span>Open control room</span
      ><Icon name="chevron" /></button
    ><button v-if="canOperator" class="settings-row" @click="runEvaluation">
      <Icon name="chart" /><span>Run pipeline checks</span
      ><Icon name="chevron" /></button
    ><button class="settings-row" @click="sheet = 'about'">
      <Icon name="book" /><span>About this demo</span
      ><Icon name="chevron" /></button
    ><button v-if="!isDemo" class="settings-row" @click="signOut()">
      <Icon name="close" /><span>Sign out</span><Icon name="chevron" />
    </button>
    <div class="demo-info">
      <p class="eyebrow">CONTEXT THAT STAYS WITH YOU</p>
      <h2>Real workflows.<br />Fictional rides.</h2>
      <p>
        Vue mobile interface. Single-agent support, durable case context, and
        policy retrieval. Multi-agent routing is optional. No API key required
        in offline mode.
      </p>
    </div>
  </section>
</template>
<script setup>
import { inject } from "vue";
import Icon from "../components/AppIcon.vue";
import AgentModePicker from "../components/AgentModePicker.vue";

const {
  isDemo,
  canRider,
  canSpecialist,
  canOperator,
  theme,
  setTheme,
  busy,
  sheet,
  agentMode,
  go,
  runEvaluation,
  identity,
  signOut,
} = inject("rideWorkspace");
</script>
