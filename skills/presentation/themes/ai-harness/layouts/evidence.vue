<script setup lang="ts">defineProps(['presentationId', 'section', 'timeCue', 'presentationOrder', 'presentationTotal'])</script>
<template><PresentationFrame layout="evidence" v-bind="$props"><slot /><template #metric><slot name="metric" /></template><template #context><slot name="context" /></template></PresentationFrame></template>
