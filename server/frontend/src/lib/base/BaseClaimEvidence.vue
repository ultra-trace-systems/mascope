<script setup>
import { computed, ref, useId, watch } from 'vue'
import RadioButton from 'primevue/radiobutton'

import {
  DETAIL_REQUIRED,
  EVIDENCE_DETAILS,
  INDEPENDENT_LINES,
  STANDARD_MEANS,
  claimOf,
  claimStatement,
  levelLabel
} from '@/lib/verification'

/**
 * What a confirmation's evidence showed, which decides the level it states
 * (docs/dev/identification_claims.md section 5.4, version 2): beside a
 * reference standard, the independent line that takes it to level 1 (none
 * states 2b); for MS/MS, a library spectrum, fragments or one loss; for
 * orthogonal evidence, what it left; for a pattern, whether the molecule-reagent
 * split is pinned. A visual look asks nothing.
 *
 * MS/MS and orthogonal evidence must say, so nothing is picked for the user
 * there and `ready` is false until something is; a pattern starts at the
 * split open, which is what the server takes when nothing is said, and a
 * standard at no independent line. Changing a verdict starts from what it
 * said (`initial`), where its evidence is the one picked. Radio buttons rather
 * than a Select, which would swallow the Escape that closes the popover
 * around it.
 *
 * The model is `{ evidence_detail, independent_line, ready, problem }`.
 */
const props = defineProps({
  /** The evidence level picked. */
  evidence: { type: String, default: null },
  /** The verdict being changed: its evidence level and `context.claim`. */
  initial: { type: Object, default: null }
})
const model = defineModel({ type: Object, default: null })

const NONE = 'none'
const id = useId()

const standard = computed(() => props.evidence === 'reference_standard')
const options = computed(() => EVIDENCE_DETAILS[props.evidence] ?? [])
const required = computed(() => DETAIL_REQUIRED.includes(props.evidence))

const detail = ref(null)
const line = ref(NONE)

// The statement the verdict being changed made. Each evidence level has words
// of its own, so what it said on other evidence matches none of these.
function start() {
  const said = claimOf(props.initial)
  line.value = said?.independent_line ?? NONE
  const known = options.value.some((option) => option.value === said?.evidence_detail)
  if (known) detail.value = said.evidence_detail
  else detail.value = props.evidence === 'pattern' ? 'split_open' : null
}
watch(() => props.evidence, start, { immediate: true })

const statement = computed(() =>
  claimStatement(props.evidence, detail.value, line.value === NONE ? null : line.value)
)

const WORDS = { msms: 'the MS/MS', orthogonal: 'the orthogonal evidence' }
const value = computed(() => {
  const missing = required.value && !detail.value
  return {
    evidence_detail: options.value.length ? detail.value : null,
    independent_line: standard.value && line.value !== NONE ? line.value : null,
    ready: !missing,
    problem: missing ? `Say what ${WORDS[props.evidence]} showed` : null
  }
})
watch(value, (next) => (model.value = next), { immediate: true })

const hint = computed(() => {
  const stated = statement.value
  if (!stated) return ''
  const label = levelLabel(stated.level, stated.letter)
  return stated.lift
    ? `${label}. Would lift it: ${stated.lift}.`
    : `${label}: a confirmed structure.`
})
</script>

<template>
  <div v-if="standard" class="claim-evidence" role="radiogroup" :aria-labelledby="`${id}-line`">
    <span :id="`${id}-line`" class="claim-label">Independent line</span>
    <span class="claim-means">{{ STANDARD_MEANS }}, and beside it:</span>
    <div class="claim-option">
      <RadioButton v-model="line" :value="NONE" :name="`${id}-line`" :inputId="`${id}-line-none`" />
      <label :for="`${id}-line-none`">None yet</label>
    </div>
    <div v-for="option in INDEPENDENT_LINES" :key="option.value" class="claim-option">
      <RadioButton
        v-model="line"
        :value="option.value"
        :name="`${id}-line`"
        :inputId="`${id}-line-${option.value}`"
      />
      <label :for="`${id}-line-${option.value}`">{{ option.label }}</label>
    </div>
    <span v-if="hint" class="claim-hint">{{ hint }}</span>
  </div>
  <div
    v-else-if="options.length"
    class="claim-evidence"
    role="radiogroup"
    :aria-labelledby="`${id}-detail`"
  >
    <span :id="`${id}-detail`" class="claim-label">What it showed</span>
    <div v-for="option in options" :key="option.value" class="claim-option">
      <RadioButton
        v-model="detail"
        :value="option.value"
        :name="`${id}-detail`"
        :inputId="`${id}-detail-${option.value}`"
      />
      <label :for="`${id}-detail-${option.value}`"
        >{{ option.label }}
        <span class="claim-level">{{ `${option.level}${option.letter ?? ''}` }}</span></label
      >
    </div>
    <span v-if="hint" class="claim-hint">{{ hint }}</span>
  </div>
</template>

<style scoped>
.claim-evidence {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.claim-label {
  font-size: 0.78rem;
  opacity: 0.8;
}
.claim-means {
  font-size: 0.75rem;
  opacity: 0.7;
}
.claim-option {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.85rem;
}
.claim-level {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  opacity: 0.7;
}
.claim-hint {
  font-size: 0.75rem;
  opacity: 0.7;
}
</style>
