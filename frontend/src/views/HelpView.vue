<template>
  <section class="page-heading">
    <p class="eyebrow">WE'RE HERE TO HELP</p>
    <h1>Let's sort it out.</h1>
    <p>No searching in circles.</p>
  </section>
  <section class="content-block top-zero">
    <button class="help-hero" @click="sheet = 'trips'">
      <span class="round-icon dark"><Icon name="chat" /></span>
      <div>
        <h2>Get help with a trip</h2>
        <p>Tell us what happened.<br />We'll connect the dots.</p>
      </div>
      <Icon name="chevron" />
    </button>
    <div class="section-title">
      <h2>Your cases</h2>
      <span>{{ cases.length }} of {{ casesTotal }} total</span>
    </div>
    <div v-if="!cases.length" class="empty-card">
      <Icon name="document" />
      <h3>A fresh start</h3>
      <p>When you open a case, you'll find every update here.</p>
    </div>
    <button
      v-for="c in cases"
      :key="c.id"
      class="case-list-item"
      @click="openCase(c.id)"
    >
      <span class="round-icon"
        ><Icon :name="c.status === 'resolved' ? 'check' : 'chat'"
      /></span>
      <div>
        <strong>{{ c.title }}</strong
        ><small>{{ c.id }} · {{ statusLabel(c.status) }}</small>
      </div>
      <Icon name="chevron" /></button
    ><button
      v-if="casesNext"
      class="secondary"
      :disabled="busy"
      @click="loadMoreCases"
    >
      Load more cases
    </button>
    <h2 class="subheading">Support that stays with you</h2>
    <details>
      <summary>How does human review work?</summary>
      <p>
        Request a review from any case. A simulated specialist can accept it in
        the Agent desk, read the complete record, and reply. No real Uber
        employee is contacted.
      </p>
    </details>
    <details>
      <summary>Will I need to explain everything again?</summary>
      <p>
        No. Your trip, original explanation, evidence, and conversation are
        saved to one case. You can correct the summary at any time in the
        conversation.
      </p>
    </details>
    <details>
      <summary>Are these real refunds?</summary>
      <p>
        No. All trips, policies, and adjustments in this demo are fictional. No
        money moves.
      </p>
    </details>
  </section>
</template>
<script setup>
import { inject } from "vue";
import Icon from "../components/AppIcon.vue";

import { statusLabel } from "../lib/rideApi.js";
const { cases, busy, sheet, casesTotal, casesNext, loadMoreCases, openCase } =
  inject("rideWorkspace");
</script>
