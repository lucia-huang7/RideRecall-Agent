<template>
  <section class="page-heading">
    <p class="eyebrow">SIMULATED SPECIALIST WORKSPACE</p>
    <h1>Agent desk</h1>
    <p>Pick up where the rider left off.</p>
  </section>
  <section class="content-block top-zero">
    <div v-if="isDemo" class="agent-note">
      <Icon name="alert" />
      <p>
        This is a local role-play workspace with public demo access. It is not
        production authentication or a live support team.
      </p>
    </div>
    <button class="secondary" @click="run(() => loadQueue({ reset: true }))">
      Refresh queue <Icon name="refresh" />
    </button>
    <div v-if="!queue.length" class="empty-card">
      <Icon name="check" />
      <h3>All clear for now</h3>
      <p>Request human review from a rider case to see it here.</p>
      <button v-if="canRider" class="text-button" @click="go('/trips')">
        Explore trips <Icon name="chevron" />
      </button>
    </div>
    <p v-if="queueTotal" class="footnote">
      {{ queue.length }} of {{ queueTotal }} cases loaded
    </p>
    <button
      v-for="c in queue"
      :key="c.id"
      class="case-list-item"
      :class="{ selected: selectedAgentId === c.id }"
      :disabled="busy"
      @click="selectAgent(c.id)"
    >
      <span class="round-icon"><Icon name="document" /></span>
      <div>
        <strong>{{ c.title }}</strong
        ><small>{{ c.id }} · {{ statusLabel(c.status) }}</small>
      </div>
      <Icon name="chevron" />
    </button>
    <button
      v-if="queueNext"
      class="secondary"
      :disabled="busy"
      @click="run(() => loadQueue({ more: true }))"
    >
      Load more cases
    </button>
    <article v-if="agentCase" class="agent-case">
      <p class="eyebrow">{{ agentCase.id }} · FULL CONTEXT ATTACHED</p>
      <h2>{{ agentCase.title }}</h2>
      <blockquote>{{ agentCase.description }}</blockquote>
      <div class="agent-summary">
        <strong>Open question</strong>
        <p>{{ agentCase.handoff.open_question }}</p>
        <strong>Evidence</strong>
        <p>
          {{ agentCase.evidence.length }} items ·
          {{ agentCase.messages.length }} messages preserved
        </p>
      </div>
      <div v-for="e in agentCase.evidence" :key="e.id" class="evidence-item">
        <Icon name="document" />
        <div>
          <strong>{{ e.filename || "Rider note" }}</strong>
          <p>{{ e.text }}</p>
        </div>
        <button
          v-if="e.filename"
          class="icon-button"
          aria-label="Download rider attachment"
          @click="download(e, true)"
        >
          <Icon name="northeast" />
        </button>
      </div>
      <details open>
        <summary>Conversation history</summary>
        <div v-for="m in agentCase.messages" :key="m.id" class="agent-history">
          <small>{{
            m.role === "rider"
              ? "Rider"
              : m.role === "agent"
                ? "Specialist"
                : m.role === "system"
                  ? "Case update"
                  : m.analysis_id
                    ? "Model analysis"
                    : "Saved response"
          }}</small>
          <p>{{ m.content }}</p>
        </div>
      </details>
      <details>
        <summary>Actions and decision history</summary>
        <div v-for="e in agentCase.events" :key="e.id" class="agent-history">
          <strong>{{ e.title }}</strong>
          <p>{{ e.detail }}</p>
        </div>
      </details>
      <button
        v-if="agentCase.status === 'waiting_for_agent'"
        class="primary"
        :disabled="busy"
        @click="staffAct('accept')"
      >
        {{ isDemo ? "Accept case as Alex" : "Accept case" }}
        <Icon name="check" /></button
      ><template
        v-else-if="
          agentCase.status === 'in_review' &&
          (isDemo || agentCase.assigned_to === identity.subject)
        "
        ><label class="field-label" for="staffReply"
          >Reply or explain your decision</label
        ><textarea
          id="staffReply"
          v-model="staffReply"
          rows="4"
          placeholder="Address the evidence and explain the next step."
        ></textarea
        ><button
          class="primary"
          :disabled="busy || !staffReply.trim()"
          @click="staffAct('reply')"
        >
          Send reply</button
        ><button
          v-if="agentOptions.includes('propose_adjustment')"
          class="secondary"
          :disabled="busy || !staffReply.trim()"
          @click="staffAct('propose_adjustment')"
        >
          Offer cancellation fee adjustment</button
        ><button
          class="secondary"
          :disabled="busy || !staffReply.trim()"
          @click="staffAct('resolve')"
        >
          Send decision and resolve
        </button></template
      >
      <p v-else class="success-note">
        {{
          agentCase.status === "in_review"
            ? "This case is assigned to another specialist."
            : agentCase.status === "awaiting_confirmation"
              ? "An adjustment is awaiting rider confirmation. No payment has been made."
              : "This case has been resolved. Its history remains available."
        }}
      </p>
      <button
        v-if="isDemo"
        class="text-button centered"
        @click="openCase(agentCase.id)"
      >
        View as rider <Icon name="northeast" />
      </button>
    </article>
  </section>
</template>
<script setup>
import { inject } from "vue";
import Icon from "../components/AppIcon.vue";

import { statusLabel } from "../lib/rideApi.js";
const {
  identity,
  isDemo,
  canRider,
  queue,
  selectedAgentId,
  busy,
  queueTotal,
  queueNext,
  agentOptions,
  staffReply,
  go,
  agentCase,
  run,
  selectAgent,
  loadQueue,
  openCase,
  download,
  staffAct,
} = inject("rideWorkspace");
</script>
