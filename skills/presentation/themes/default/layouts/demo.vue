<script setup lang="ts">defineProps(['presentationId', 'section', 'timeCue', 'presentationOrder', 'presentationTotal'])</script>
<template><PresentationFrame layout="demo" v-bind="$props"><slot /><template #input><slot name="input" /></template><template #action><slot name="action" /></template><template #result><slot name="result" /></template></PresentationFrame></template>
