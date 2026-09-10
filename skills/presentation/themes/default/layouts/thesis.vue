<script setup lang="ts">defineProps(['presentationId', 'section', 'timeCue', 'presentationOrder', 'presentationTotal'])</script>
<template><PresentationFrame layout="thesis" v-bind="$props"><slot /><template #route><slot name="route" /></template></PresentationFrame></template>
