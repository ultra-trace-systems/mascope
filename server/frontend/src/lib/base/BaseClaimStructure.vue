<script setup>
import { computed, ref, useId, watch } from 'vue'
import InputText from 'primevue/inputtext'
import RadioButton from 'primevue/radiobutton'

import { isValidInchikey } from '@/lib/chem'
import { claimLevel, levelLabel, needsStructure } from '@/lib/verification'

/**
 * The structure a confirmation names, picked from the row's candidates: an
 * identification claim (docs/dev/identification_claims.md section 5).
 *
 * The candidates come first, the target's declaration before the reference
 * identities (`claimCandidates`); a free InChIKey and name is the last option,
 * shown as *unlisted*. On evidence that is a statement about a structure (a
 * reference standard, MS/MS, orthogonal) a structure is required, and the first
 * candidate is picked for the user, so the requirement costs one look rather
 * than a paste. On a pattern or a visual look naming one is optional and none
 * is picked. Changing a verdict starts from the structure it names (`initial`):
 * its candidate, else *unlisted* with its InChIKey and name, so a new note or
 * evidence level does not swap the claim behind the user's back. Radio buttons
 * rather than a Select, which would swallow the Escape that closes the popover
 * around it.
 *
 * The model is `{ claimed_inchikey, claimed_name, ready, problem }`: `ready` is
 * false while a required structure is missing or an unlisted InChIKey is not
 * one, and `problem` then says which. An empty unlisted key on evidence that
 * needs no structure names none.
 */
const props = defineProps({
  /** From `claimCandidates`: `{ inchikey, name, label }` each. */
  candidates: { type: Array, default: () => [] },
  /** The evidence level picked; decides whether a structure is required. */
  evidence: { type: String, default: null },
  /** The verdict being changed: its `claimed_inchikey` and `claimed_name`. */
  initial: { type: Object, default: null }
})
const model = defineModel({ type: Object, default: null })

const NONE = 'none'
const UNLISTED = 'unlisted'
const id = useId()

const required = computed(() => needsStructure(props.evidence))
const choice = ref(NONE)
const touched = ref(false) // the user picked, so a change of evidence keeps it
const unlistedKey = ref('')
const unlistedName = ref('')

const firstChoice = () => props.candidates[0]?.inchikey ?? UNLISTED
const initialKey = computed(() => {
  const key = props.initial?.claimed_inchikey
  return typeof key === 'string' && key.trim() ? key.trim() : null
})
// The structure the verdict being changed names: its candidate, else unlisted.
function initialChoice() {
  const key = initialKey.value
  if (props.candidates.some((candidate) => candidate.inchikey === key)) return key
  unlistedKey.value = key
  unlistedName.value = props.initial?.claimed_name ?? ''
  return UNLISTED
}

// Pre-fill: the structure the verdict being changed names; else the first
// candidate when a structure is required, none otherwise - until the user
// picks; a requirement that arrives later still fills an empty choice.
watch(
  [required, initialKey, () => props.candidates.map((candidate) => candidate.inchikey).join()],
  () => {
    if (!touched.value) {
      if (initialKey.value) choice.value = initialChoice()
      else choice.value = required.value ? firstChoice() : NONE
    } else if (required.value && choice.value === NONE) choice.value = firstChoice()
  },
  { immediate: true }
)

function pick(value) {
  choice.value = value
  touched.value = true
}

const unlistedBlank = computed(() => !unlistedKey.value.trim())
const unlistedValid = computed(() => !unlistedBlank.value && isValidInchikey(unlistedKey.value))

const MISSING = 'Name the structure this evidence identifies'
const MALFORMED = 'Not an InChIKey: it reads like WQZGKKKJIJFFOK-GASJEMHNSA-N'
const none = (ready, problem) => ({ claimed_inchikey: null, claimed_name: null, ready, problem })

const claim = computed(() => {
  if (choice.value === UNLISTED) {
    if (unlistedBlank.value) return required.value ? none(false, MISSING) : none(true, null)
    if (!unlistedValid.value) return none(false, MALFORMED)
    return {
      claimed_inchikey: unlistedKey.value.trim(),
      claimed_name: unlistedName.value.trim() || null,
      ready: true,
      problem: null
    }
  }
  const candidate = props.candidates.find((item) => item.inchikey === choice.value)
  if (candidate) {
    return {
      claimed_inchikey: candidate.inchikey,
      claimed_name: candidate.name,
      ready: true,
      problem: null
    }
  }
  return required.value ? none(false, MISSING) : none(true, null)
})
watch(claim, (value) => (model.value = value), { immediate: true })

const level = computed(() => levelLabel(claimLevel(props.evidence)))
const hint = computed(() => {
  if (!props.evidence) return ''
  if (required.value) return `${level.value}: a statement about this structure.`
  if (props.evidence === 'pattern')
    return `${level.value}: the formula; a structure raises nothing.`
  return 'No level of its own: the row keeps the engine’s.'
})
</script>

<template>
  <div class="claim-structure" role="radiogroup" :aria-labelledby="`${id}-label`">
    <span :id="`${id}-label`" class="claim-label"
      >Structure{{ required ? '' : ' (optional)' }}</span
    >
    <div v-if="!required" class="claim-option">
      <RadioButton
        :modelValue="choice"
        :value="NONE"
        :name="`${id}-structure`"
        :inputId="`${id}-none`"
        @update:modelValue="pick"
      />
      <label :for="`${id}-none`">No structure</label>
    </div>
    <div v-for="candidate in candidates" :key="candidate.inchikey" class="claim-option">
      <RadioButton
        :modelValue="choice"
        :value="candidate.inchikey"
        :name="`${id}-structure`"
        :inputId="`${id}-${candidate.inchikey}`"
        @update:modelValue="pick"
      />
      <label :for="`${id}-${candidate.inchikey}`" v-tooltip.top="candidate.inchikey"
        >{{ candidate.label }} · {{ candidate.name ?? candidate.inchikey }}</label
      >
    </div>
    <div class="claim-option">
      <RadioButton
        :modelValue="choice"
        :value="UNLISTED"
        :name="`${id}-structure`"
        :inputId="`${id}-unlisted`"
        @update:modelValue="pick"
      />
      <label :for="`${id}-unlisted`">Unlisted: an InChIKey of my own</label>
    </div>
    <template v-if="choice === UNLISTED">
      <InputText
        v-model="unlistedKey"
        @update:modelValue="touched = true"
        placeholder="InChIKey"
        aria-label="InChIKey"
        size="small"
        fluid
        :invalid="Boolean(unlistedKey.trim()) && !unlistedValid"
      />
      <InputText
        v-model="unlistedName"
        @update:modelValue="touched = true"
        placeholder="Name (optional)"
        aria-label="Structure name"
        size="small"
        fluid
      />
    </template>
    <span v-if="hint" class="claim-hint">{{ hint }}</span>
  </div>
</template>

<style scoped>
.claim-structure {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.claim-label {
  font-size: 0.78rem;
  opacity: 0.8;
}
.claim-option {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.85rem;
}
.claim-hint {
  font-size: 0.75rem;
  opacity: 0.7;
}
</style>
