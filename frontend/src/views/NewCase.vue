<template>
  <template v-if="trip"
    ><section class="content-block">
      <p class="eyebrow">LET'S MAKE THIS RIGHT</p>
      <h1>What happened?</h1>
      <p>
        You don't need to find the perfect category.<br />Just tell us in your
        own words.
      </p>
      <div class="linked-trip">
        <Icon name="route" />
        <div>
          <strong>{{ trip.destination }}</strong
          ><small>{{ trip.date }} · {{ money(trip.total) }}</small>
        </div>
        <span>Linked</span>
      </div>
      <fieldset class="topic-options">
        <legend>What can we help with?</legend>
        <button
          v-for="option in topics"
          :key="option.id"
          :class="{ selected: topic === option.id }"
          @click="selectTopic(option)"
        >
          {{ option.label }}<Icon v-if="topic === option.id" name="check" />
        </button>
      </fieldset>
      <label class="field-label" for="description">Tell us a little more</label
      ><textarea
        id="description"
        v-model="description"
        maxlength="4000"
        rows="5"
        placeholder="Tell us what happened and what you would like us to review."
      ></textarea>
      <p class="field-hint">
        Your trip details are already attached. No need to repeat them.
      </p>
      <AgentModePicker v-model="agentMode" :disabled="busy" /><button
        class="primary"
        :disabled="busy || description.trim().length < 5"
        @click="createCase()"
      >
        {{ busy ? "Opening your case…" : "Continue with support"
        }}<Icon name="arrow" class="arrow-right" /></button
      ><button
        class="text-button centered"
        :disabled="busy"
        @click="createCase(true)"
      >
        I'd prefer a human review <Icon name="chevron" />
      </button>
      <p class="footnote">
        {{
          engineStatus.mode === "live"
            ? "A model provider analyzes this fictional case. Uber is not contacted."
            : "Local execution. No information is sent to Uber or an AI provider."
        }}
      </p>
    </section></template
  >
</template>
<script setup>
import { inject } from "vue";
import Icon from "../components/AppIcon.vue";
import AgentModePicker from "../components/AgentModePicker.vue";

import { money, statusLabel } from "../lib/rideApi.js";
const {
  busy,
  description,
  topic,
  agentMode,
  engineStatus,
  route,
  topics,
  trip,
  selectTopic,
  createCase,
} = inject("rideWorkspace");
</script>
