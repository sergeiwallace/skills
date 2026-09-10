<script setup lang="ts">defineProps(['presentationId', 'section', 'timeCue', 'presentationOrder', 'presentationTotal'])</script>
<template><PresentationFrame layout="comparison" v-bind="$props"><slot /><template #left><slot name="left" /></template><template #right><slot name="right" /></template></PresentationFrame></template>
