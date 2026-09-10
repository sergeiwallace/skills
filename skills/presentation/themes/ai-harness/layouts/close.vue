<script setup lang="ts">defineProps(['presentationId', 'section', 'timeCue', 'presentationOrder', 'presentationTotal'])</script>
<template><PresentationFrame layout="close" v-bind="$props"><slot /><template #actions><slot name="actions" /></template></PresentationFrame></template>
