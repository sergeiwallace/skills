<script setup lang="ts">defineProps(['presentationId', 'section', 'timeCue', 'presentationOrder', 'presentationTotal'])</script>
<template><PresentationFrame layout="code" v-bind="$props"><slot /><template #code><slot name="code" /></template><template #explanation><slot name="explanation" /></template></PresentationFrame></template>
