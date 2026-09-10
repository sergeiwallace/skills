<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  layout: string
  presentationId?: string
  section?: string
  timeCue?: string
  presentationOrder?: number
  presentationTotal?: number
}>()

const progress = computed(() => {
  if (!props.presentationOrder || !props.presentationTotal)
    return 0
  return Math.min(100, (props.presentationOrder / props.presentationTotal) * 100)
})
</script>

<template>
  <div
    class="slidev-layout presentation-layout"
    :class="'presentation-layout--' + layout"
    :data-presentation-id="presentationId"
    :data-presentation-layout="layout"
  >
    <div class="presentation-chrome" aria-hidden="true">
      <span class="presentation-section">{{ section }}</span>
      <span>{{ presentationOrder }} / {{ presentationTotal }}</span>
      <span v-if="timeCue" class="presentation-time">{{ timeCue }}</span>
    </div>
    <main class="presentation-region" data-presentation-slot="body"><slot /></main>
    <section v-if="$slots.subtitle" class="presentation-region" data-presentation-slot="subtitle"><slot name="subtitle" /></section>
    <section v-if="$slots.route" class="presentation-region" data-presentation-slot="route"><slot name="route" /></section>
    <section v-if="$slots.left" class="presentation-region" data-presentation-slot="left"><slot name="left" /></section>
    <section v-if="$slots.right" class="presentation-region" data-presentation-slot="right"><slot name="right" /></section>
    <section v-if="$slots.steps" class="presentation-region" data-presentation-slot="steps"><slot name="steps" /></section>
    <section v-if="$slots.metric" class="presentation-region" data-presentation-slot="metric"><slot name="metric" /></section>
    <section v-if="$slots.context" class="presentation-region" data-presentation-slot="context"><slot name="context" /></section>
    <section v-if="$slots.code" class="presentation-region" data-presentation-slot="code"><slot name="code" /></section>
    <section v-if="$slots.explanation" class="presentation-region" data-presentation-slot="explanation"><slot name="explanation" /></section>
    <section v-if="$slots.input" class="presentation-region" data-presentation-slot="input"><slot name="input" /></section>
    <section v-if="$slots.action" class="presentation-region" data-presentation-slot="action"><slot name="action" /></section>
    <section v-if="$slots.result" class="presentation-region" data-presentation-slot="result"><slot name="result" /></section>
    <section v-if="$slots.actions" class="presentation-region" data-presentation-slot="actions"><slot name="actions" /></section>
    <div class="presentation-progress" aria-hidden="true"><span :style="{ width: progress + '%' }" /></div>
  </div>
</template>
