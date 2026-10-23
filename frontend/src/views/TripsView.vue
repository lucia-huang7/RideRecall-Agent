<template>
  <section class="page-heading">
    <p class="eyebrow">YOUR JOURNEY, TAKEN CARE OF</p>
    <h1>Your trips</h1>
    <p>Good rides. Better support.</p>
  </section>
  <section
    v-if="activeCases.length"
    class="active-strip"
    aria-label="Active cases"
  >
    <button @click="openCase(activeCases[0].id)">
      <span class="round-icon"><Icon name="chat" /></span
      ><span
        ><strong>Your case is moving forward</strong
        ><small
          >{{ statusLabel(activeCases[0].status) }} ·
          {{ activeCases[0].id }}</small
        ></span
      ><Icon name="chevron" />
    </button>
  </section>
  <div class="section-title">
    <h2>Recent activity</h2>
    <span>October 2026</span>
  </div>
  <section class="trip-list">
    <article v-for="(trip, index) in trips" :key="trip.id" class="trip-card">
      <button
        class="trip-open"
        :aria-label="`View trip to ${trip.destination}`"
        @click="go('/trip/' + trip.id)"
      >
        <div class="vehicle-tile" :class="trip.color">
          <svg viewBox="0 0 90 50" aria-hidden="true">
            <ellipse
              cx="46"
              cy="42"
              rx="36"
              ry="4"
              fill="#000000"
              opacity=".08"
            />
            <path d="m12 29 10-7 9-12h27l15 14 7 5v10H10V31Z" fill="#383838" />
            <path d="m33 13-7 10h19V13Zm16 0v10h18L56 13Z" fill="#c3c3c3" />
            <path d="M13 29h64" stroke="#787878" stroke-width="2" />
            <circle cx="26" cy="38" r="7" fill="#1a1a1a" />
            <circle cx="65" cy="38" r="7" fill="#1a1a1a" />
            <circle cx="26" cy="38" r="3" fill="#acacac" />
            <circle cx="65" cy="38" r="3" fill="#acacac" />
          </svg>
        </div>
        <span class="trip-text"
          ><strong>{{ trip.destination }}</strong
          ><small>{{ trip.date }} · {{ trip.time }}</small
          ><span class="trip-meta"
            >{{ trip.vehicle }}
            <span v-if="trip.status === 'Canceled'" class="canceled"
              >Canceled</span
            ></span
          ></span
        ><span class="trip-price"
          >{{ money(trip.total) }}<Icon name="chevron"
        /></span>
      </button>
      <button class="trip-help" @click="start(trip)">
        <Icon name="chat" />{{
          index === 0
            ? "Charged for a canceled trip?"
            : "Get help with this trip"
        }}<Icon name="arrow" class="arrow-right" />
      </button>
    </article>
  </section>
  <section class="support-promise">
    <span class="round-icon"><Icon name="route" /></span>
    <h2>You explain it once.<br />We take it from there.</h2>
    <p>
      Your trip, messages, and evidence stay together—even when a specialist
      steps in.
    </p>
    <button class="text-button" @click="go('/help')">
      How support works <Icon name="northeast" />
    </button>
  </section>
</template>
<script setup>
import { inject } from "vue";
import Icon from "../components/AppIcon.vue";

import { money, statusLabel } from "../lib/rideApi.js";
const { trips, go, activeCases, start, openCase } = inject("rideWorkspace");
</script>
