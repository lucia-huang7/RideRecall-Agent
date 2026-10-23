<template>
  <template v-if="currentCase"
    ><section class="content-block case-heading">
      <div class="case-overline">
        <span class="eyebrow">{{ currentCase.id }}</span
        ><button class="text-button" @click="sheet = 'trace'">
          Behind the scenes <Icon name="panel" />
        </button>
      </div>
      <h1>{{ currentCase.title }}</h1>
      <span class="status-pill" :class="currentCase.status"
        ><span class="tiny-dot"></span
        >{{ statusLabel(currentCase.status) }}</span
      >
      <p class="case-description">{{ currentCase.description }}</p>
    </section>
    <section class="next-card">
      <span class="round-icon dark"
        ><Icon
          :name="
            currentCase.status === 'resolved'
              ? 'check'
              : currentCase.status === 'awaiting_confirmation'
                ? 'document'
                : 'chat'
          "
      /></span>
      <div>
        <p class="eyebrow">
          {{
            currentCase.status === "resolved"
              ? "YOUR OUTCOME"
              : "WHAT HAPPENS NEXT"
          }}
        </p>
        <h2>{{ nextStep.title }}</h2>
        <p>{{ nextStep.text }}</p>
      </div>
      <button
        v-if="currentCase.status === 'awaiting_confirmation'"
        class="primary"
        :disabled="busy"
        @click="sheet = 'confirm'"
      >
        Review {{ money(currentCase.proposal.amount) }} adjustment
        <Icon name="arrow" class="arrow-right" /></button
      ><button
        v-else-if="currentCase.status === 'needs_review'"
        class="primary"
        :disabled="busy"
        @click="act('handoff')"
      >
        Request human review
        <Icon name="arrow" class="arrow-right" /></button
      ><button
        v-else-if="currentCase.status === 'awaiting_rider'"
        class="primary"
        :disabled="busy"
        @click="act('close')"
      >
        This explains my charge <Icon name="check" /></button
      ><button
        v-if="['waiting_for_agent', 'in_review'].includes(currentCase.status)"
        class="secondary"
        @click="go('/agent')"
      >
        Try the simulated Agent desk <Icon name="northeast" />
      </button>
    </section>
    <section class="content-block">
      <div class="section-title flush">
        <h2>Your trip</h2>
        <button class="text-button" @click="go('/trip/' + trip.id)">
          Details <Icon name="chevron" />
        </button>
      </div>
      <div class="linked-trip">
        <Icon name="route" />
        <div>
          <strong>{{ trip.destination }}</strong
          ><small>{{ trip.date }} · {{ trip.status }}</small>
        </div>
        <strong>{{ money(trip.total) }}</strong>
      </div>
      <h2 class="subheading">What we've checked</h2>
      <div v-for="fact in currentCase.facts" :key="fact.label" class="fact-row">
        <Icon
          :name="fact.source === 'Requires verification' ? 'alert' : 'check'"
        />
        <div>
          <strong>{{ fact.label }}</strong>
          <p>{{ fact.text }}</p>
          <small>{{ fact.source }}</small>
        </div>
      </div>
      <section v-if="latestRun?.citations.length" class="retrieved-evidence">
        <h2 class="subheading">Sources used in this analysis</h2>
        <details
          v-for="d in latestRun.citations"
          :key="d.document_id + ':' + d.chunk"
          class="source-record"
        >
          <summary>
            {{ d.title }}
            <small>[{{ d.document_id.slice(0, 8) }}:{{ d.chunk }}]</small>
          </summary>
          <p>{{ d.content }}</p>
          <small
            >Retrieved from the local policy index ·
            {{
              latestRun.mode === "demo"
                ? "Hash vectors, not neural embeddings"
                : "Local hash vector retrieval"
            }}</small
          >
        </details>
      </section>
      <button class="policy-link" @click="sheet = 'policy'">
        <Icon name="book" /><span
          >See the policy behind this review<small
            >Fictional demo policy · Chicago</small
          ></span
        ><Icon name="chevron" />
      </button>
      <div class="section-title">
        <h2>Your evidence</h2>
        <button class="text-button" @click="sheet = 'evidence'">
          Add <Icon name="plus" />
        </button>
      </div>
      <p v-if="!currentCase.evidence.length" class="muted">
        Add a note or screenshot. Your original explanation is already saved.
      </p>
      <div v-for="e in currentCase.evidence" :key="e.id" class="evidence-item">
        <Icon name="document" />
        <div>
          <strong>{{ e.filename || "Your note" }}</strong>
          <p>{{ e.text }}</p>
          <small>Rider-provided · Not independently verified</small>
        </div>
        <button
          v-if="e.filename"
          class="icon-button"
          aria-label="Download attachment"
          @click="download(e)"
        >
          <Icon name="northeast" />
        </button>
      </div>
      <h2 class="subheading">Every step, in one place</h2>
      <ol class="timeline">
        <li v-for="(item, index) in currentCase.events" :key="item.id">
          <span
            class="timeline-dot"
            :class="{ latest: index === currentCase.events.length - 1 }"
          ></span
          ><strong>{{ item.title }}</strong>
          <p>{{ item.detail }}</p>
          <small
            >{{ time(item.at) }} ·
            {{
              item.actor === "agent"
                ? "Demo specialist"
                : item.actor === "rider"
                  ? "You"
                  : item.actor === "system"
                    ? "LLM-Agent-System"
                    : item.actor
            }}</small
          >
        </li>
      </ol>
      <button class="primary" @click="go('/case/' + currentCase.id + '/chat')">
        <Icon name="chat" /> Continue the conversation</button
      ><button
        v-if="!['waiting_for_agent', 'in_review'].includes(currentCase.status)"
        class="secondary"
        :disabled="busy"
        @click="act('handoff')"
      >
        {{
          currentCase.status === "resolved"
            ? "Request another review"
            : "Talk to a specialist"
        }}
      </button>
      <p class="footnote">
        No real support request or payment is sent to Uber.
      </p>
    </section></template
  >
</template>
<script setup>
import { inject } from "vue";
import Icon from "../components/AppIcon.vue";

import { money, statusLabel } from "../lib/rideApi.js";
const {
  currentCase,
  busy,
  sheet,
  latestRun,
  go,
  trip,
  nextStep,
  time,
  act,
  download,
} = inject("rideWorkspace");
</script>
