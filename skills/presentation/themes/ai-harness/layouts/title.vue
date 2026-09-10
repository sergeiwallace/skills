<script setup lang="ts">defineProps(['presentationId', 'section', 'timeCue', 'presentationOrder', 'presentationTotal'])</script>
<template><PresentationFrame layout="title" v-bind="$props"><slot /><template #subtitle><slot name="subtitle" /></template></PresentationFrame></template>
