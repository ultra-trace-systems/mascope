<script setup>
import { ref, reactive, computed, watch } from 'vue'

import Popover from 'primevue/popover'
import FloatLabel from 'primevue/floatlabel'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import { useConfirm } from 'primevue/useconfirm'

import { isValidChemicalFormula, findExistingCompound } from '@/lib/chem'
import { useApp } from '@/stores'

const confirm = useConfirm()
const app = useApp()

const props = defineProps({
  collection: {
    required: false,
    type: Object
  },
  formula: {
    required: false,
    type: String
  },
  formulaEditable: {
    type: Boolean,
    default: true
  }
})

const popover = ref()

const existingCompoundName = ref(null)

const input = reactive({
  target_compound_formula: props.formula ?? '',
  target_compound_name: existingCompoundName.value ?? null
})

const targetCollection = computed(() => app.data.target.collection.detailed ?? props.collection)
const targetCompounds = computed(() => app.data.target.collection.detailed?.target_compounds ?? [])

// Check for existing compound based on input. The quick path asks for a
// formula and a name only; the CAS number and the structure are set in
// Edit compound.
const existingCompound = computed(() =>
  findExistingCompound(app.data.target.compound.list, {
    target_compound_formula: input.target_compound_formula,
    target_compound_name: input.target_compound_name
  })
)

// Check if existing compound is already in collection
const alreadyInCollection = computed(
  () =>
    existingCompound.value &&
    targetCompounds.value.some(
      (comp) => comp.target_compound_id === existingCompound.value.target_compound_id
    )
)

// Auto-populate name field when existing compound is found
// Prefer a compound with a name over one without
watch(
  () => existingCompound.value,
  (compound) => {
    if (!compound) return

    const existing = app.data.target.compound.list.filter(
      ({ target_compound_formula }) => target_compound_formula === compound.target_compound_formula
    )
    const compoundWithName = existing.find((comp) => comp.target_compound_name?.trim()) ?? compound
    if (compoundWithName) {
      input.target_compound_name = compoundWithName.target_compound_name
    }
  },
  { immediate: true }
)

// UI status and button configuration
const buttonConfig = computed(() => {
  if (!input.target_compound_formula.trim()) {
    return {
      label: 'Add',
      severity: undefined,
      tooltip: 'Enter formula to add compound'
    }
  }
  if (invalidFormula.value) {
    return {
      label: `Add compound`,
      tooltip: 'Invalid chemical formula'
    }
  }
  if (alreadyInCollection.value) {
    return {
      label: 'Add',
      tooltip: 'Compound is already in collection'
    }
  }
  if (existingCompound.value) {
    return {
      label: 'Add',
      severity: 'info',
      tooltip: 'Add existing compound'
    }
  }
  return {
    label: 'Create',
    severity: 'primary',
    tooltip: 'Create new compound'
  }
})

const addCompound = async () => {
  const common = {
    target_collection_id: targetCollection.value.target_collection_id,
    target_collection_name: targetCollection.value.target_collection_name,
    target_collection_type: targetCollection.value.target_collection_type
  }
  if (existingCompound.value) {
    await app.data.target.collection.update({
      ...common,
      target_compound_ids: [
        ...new Set([
          ...targetCompounds.value.map(({ target_compound_id }) => target_compound_id),
          existingCompound.value.target_compound_id
        ])
      ]
    })
  } else {
    await app.data.target.collection.update({
      ...common,
      target_compound_ids: targetCompounds.value.map(
        ({ target_compound_id }) => target_compound_id
      ),
      target_compounds_create: [
        {
          target_compound_formula: input.target_compound_formula.trim(),
          target_compound_name: input.target_compound_name?.trim()
        }
      ]
    })
  }
  // Refresh matches for focused sample
  if (app.data.sample.focusedId) {
    app.data.sample.rematch(app.data.sample.focused)
  }
  // Clear form
  Object.assign(input, {
    target_compound_formula: '',
    target_compound_name: null
  })

  popover.value.hide()
}

const confirmation = async () => {
  const batchCount = app.data.target.collection.detailed?.sample_batches_count ?? 0
  const isCalibrants = targetCollection.value?.target_collection_type === 'CALIBRANTS'
  if (isCalibrants) {
    confirm.require({
      icon: 'pi pi-exclamation-triangle',
      header: 'Edit calibration collection',
      message:
        'Adding a compound to a calibration collection affects how associated samples are calibrated. ' +
        'Are you sure you want to proceed?',
      accept: addCompound,
      acceptProps: {
        icon: 'pi pi-plus',
        label: 'Add',
        severity: 'warn'
      },
      rejectProps: {
        icon: 'pi pi-times',
        label: 'Cancel',
        severity: 'secondary'
      }
    })
  } else if (batchCount > 1) {
    confirm.require({
      icon: 'pi pi-exclamation-triangle',
      header: `Add compound to ${batchCount} batches`,
      message: `Are you sure you want to add compound ${input.target_compound_formula.trim()} to a collection used in ${batchCount} batches?`,
      accept: addCompound,
      acceptProps: {
        icon: 'pi pi-plus',
        label: 'Add'
      },
      rejectProps: {
        icon: 'pi pi-times',
        label: 'Cancel',
        severity: 'secondary'
      }
    })
  } else {
    addCompound()
  }
}

const invalidFormula = computed(
  () =>
    input.target_compound_formula.length > 0 &&
    !isValidChemicalFormula(input.target_compound_formula)
)
const plusButtonDisabled = computed(() => !props.collection && !app.data.match.collection.focused)
const addButtonDisabled = computed(
  () => !input.target_compound_formula.trim() || invalidFormula.value || alreadyInCollection.value
)
</script>

<template>
  <Button
    icon="pi pi-plus"
    text
    size="small"
    @click="
      (event) => {
        event.stopPropagation()
        popover.toggle(event)
      }
    "
    v-tooltip.left="
      plusButtonDisabled
        ? 'Open a target collection to add a compound'
        : `Add compound to ${targetCollection?.target_collection_name ?? 'selected collection'}`
    "
    :disabled="plusButtonDisabled"
  />
  <Popover ref="popover">
    <div class="col" style="gap: 0rem">
      <h4 style="margin: 1rem 0">
        Add compound to '{{ targetCollection?.target_collection_name ?? 'selected collection' }}'
      </h4>
      <FloatLabel style="margin: 1rem 0">
        <InputText
          id="add-compound-formula"
          v-model="input.target_compound_formula"
          :invalid="input.target_compound_formula.length > 0 && invalidFormula"
          required
          autofocus="true"
          :disabled="!props.formulaEditable"
        />
        <label for="add-compound-formula"> Formula </label>
      </FloatLabel>
      <FloatLabel style="margin: 1rem 0">
        <InputText
          id="add-compound-name"
          v-model="input.target_compound_name"
          :disabled="Boolean(existingCompound)"
        />
        <label for="add-compound-name"> Name </label>
      </FloatLabel>
      <Button
        :label="buttonConfig?.label ?? 'Add'"
        icon="pi pi-plus"
        style="width: 100%"
        @click="confirmation"
        :disabled="addButtonDisabled"
        :severity="buttonConfig?.severity ?? 'primary'"
        v-tooltip="buttonConfig?.tooltip ?? 'Add compound'"
      />
    </div>
  </Popover>
</template>
