<script setup lang="ts">defineProps(['presentationId', 'section', 'timeCue', 'presentationOrder', 'presentationTotal'])</script>
<template><PresentationFrame layout="process" v-bind="$props"><slot /><template #steps><slot name="steps" /></template></PresentationFrame></template>
