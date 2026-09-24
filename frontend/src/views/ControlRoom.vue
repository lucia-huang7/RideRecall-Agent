<template><OperationsPanel :identity="identity" /></template>
<script setup>
import { inject } from "vue";

import OperationsPanel from "../OperationsPanel.vue";
const { identity } = inject("rideWorkspace");
</script>
