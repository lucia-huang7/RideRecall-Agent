<template>
  <template v-if="trip"
    ><RideMap :tone="trip.color" :canceled="trip.status === 'Canceled'" />
    <section class="content-block">
      <p class="eyebrow">{{ trip.date }} · {{ trip.time }}</p>
      <h1>{{ trip.destination }}</h1>
      <p>{{ trip.vehicle }} · {{ trip.duration }}</p>
      <div class="route-summary">
        <span class="route-dot"></span>
        <div>
          <small>Pickup</small><strong>{{ trip.pickup }}</strong>
        </div>
        <span class="route-square"></span>
        <div>
          <small>Destination</small><strong>{{ trip.destination }}</strong>
        </div>
      </div>
      <div class="receipt">
        <div>
          <span>{{
            trip.status === "Canceled" ? "Cancellation fee" : "Trip fare"
          }}</span
          ><strong>{{ money(trip.total) }}</strong>
        </div>
        <div v-if="trip.estimated">
          <span>Original estimate</span><span>{{ money(trip.estimated) }}</span>
        </div>
        <div><span>Payment status</span><span>Settled · Demo card</span></div>
      </div>
      <button class="primary" @click="start(trip)">
        <Icon name="chat" /> Get help with this trip</button
      ><button v-if="tripCase" class="secondary" @click="openCase(tripCase.id)">
        View existing case <Icon name="chevron" />
      </button>
      <p class="footnote">All trip and payment records are fictional.</p>
    </section></template
  >
</template>
<script setup>
import { inject } from "vue";
import Icon from "../components/AppIcon.vue";

import RideMap from "../RideMap.vue";

import { money, statusLabel } from "../lib/rideApi.js";
const { route, trip, tripCase, time, start, openCase } =
  inject("rideWorkspace");
</script>
