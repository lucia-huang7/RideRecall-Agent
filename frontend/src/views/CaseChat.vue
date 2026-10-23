<template>
  <template v-if="currentCase"
    ><button class="chat-case-bar" @click="openCase(currentCase.id)">
      <span class="round-icon"><Icon name="document" /></span
      ><span
        ><strong>{{ currentCase.title }}</strong
        ><small
          >{{ statusLabel(currentCase.status) }} · {{ currentCase.id }}</small
        ></span
      ><Icon name="chevron" />
    </button>
    <section class="messages" aria-label="Case conversation" aria-live="polite">
      <p class="chat-date">Your context stays with this case</p>
      <article
        v-for="m in currentCase.messages"
        :key="m.id"
        class="message"
        :class="m.role"
      >
        <span v-if="m.role !== 'rider'" class="sender">{{
          m.role === "agent"
            ? m.staff_subject
              ? m.staff_subject + " · Specialist"
              : "Alex · Demo specialist"
            : m.role === "system"
              ? "Case update"
              : !m.analysis_id
                ? "Saved assistant response"
                : m.model_mode === "live"
                  ? "LLM-Agent-System · Model analysis"
                  : "LLM-Agent-System · Offline model adapter"
        }}</span>
        <div>{{ m.content }}</div>
        <small>{{ time(m.at) }}</small>
      </article>
    </section>
    <div class="composer-wrap">
      <AgentModePicker v-model="agentMode" :disabled="busy" compact />
      <div class="chat-quick">
        <button
          :disabled="
            busy ||
            ['waiting_for_agent', 'in_review'].includes(currentCase.status)
          "
          @click="act('handoff')"
        >
          {{
            ["waiting_for_agent", "in_review"].includes(currentCase.status)
              ? "Human review requested"
              : "Request human review"
          }}</button
        ><button @click="sheet = 'evidence'">Add evidence</button>
      </div>
      <form class="composer" @submit.prevent="sendMessage">
        <input
          v-model="draft"
          aria-label="Message support"
          placeholder="Message about this case…"
          maxlength="4000"
        /><button
          type="submit"
          :disabled="busy || !draft.trim()"
          aria-label="Send message"
        >
          <Icon name="arrow" />
        </button>
      </form>
      <p>
        {{ agentMode === "multi" ? "Optional multi-agent" : "Single agent" }}
        · Durable context ·
        {{
          engineStatus.mode === "live"
            ? "Live model analysis · Simulated data"
            : "Offline model adapter"
        }}
      </p>
    </div></template
  >
</template>
<script setup>
import { inject } from "vue";
import Icon from "../components/AppIcon.vue";
import AgentModePicker from "../components/AgentModePicker.vue";

import { statusLabel } from "../lib/rideApi.js";
const {
  currentCase,
  busy,
  sheet,
  draft,
  agentMode,
  engineStatus,
  time,
  openCase,
  act,
  sendMessage,
} = inject("rideWorkspace");
</script>
