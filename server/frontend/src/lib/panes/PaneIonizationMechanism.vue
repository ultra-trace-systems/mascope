<script setup>
/**
 * Component for managing ionization mechanisms
 *
 * Allows adding and removing mechanisms with validation. A mechanism is typed
 * in the standard adduct notation (`[M+H]+`, `[M-H]-`, `[M]+.`); the legacy
 * spelling (`+H+`, `-H+`, `+`) is accepted too, and the server stores either
 * in the standard one.
 */
import { reactive, computed, watch } from 'vue'

import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import FloatLabel from 'primevue/floatlabel'
import Message from 'primevue/message'
import { useConfirm } from 'primevue/useconfirm'

import { isValidChemicalFormula } from '@/lib/chem'
import { mechanismProblem, mechanismTerms, standardMechanism } from '@/lib/mechanism'
import { useApp } from '@/stores'

const app = useApp()
const confirm = useConfirm()

const add = reactive({
  mechanism: ''
})

const resetFields = () => {
  add.mechanism = ''
}

// Why the typed mechanism cannot be added, or null: the notation first, then
// each term as a formula of element symbols. A labelled atom is written with a
// caret (^N); the bracketed form ([15N]) is refused here, as it always was,
// because target ions are built from the caret form alone. Whether the symbols
// are real elements is the server's check.
const problem = computed(() => {
  const text = add.mechanism.trim()
  if (!text) return null
  const notation = mechanismProblem(text)
  if (notation) return notation
  const term = mechanismTerms(text).find((term) => !isValidChemicalFormula(term))
  return term ? `'${term}' is not a formula; a labelled atom is written with a caret, ^N` : null
})

// Under the field: what is wrong, else the spelling the server will store when
// it is not what was typed (a legacy spelling, or terms in another order than
// the alphabetical one a mechanism is written in), else examples.
const hint = computed(() => {
  if (problem.value) return problem.value
  const text = add.mechanism.trim()
  const stored = text ? standardMechanism(text) : text
  if (stored !== text) return `Stored as ${stored}`
  return 'For example [M+H]+, [M-H]-, [M+Br]-, or [M]+. for electron transfer'
})

// reset when create successful
watch(
  computed(() => app.data.ionization.mechanism.list.length),
  (newVal, oldVal) => {
    if (newVal > oldVal) {
      resetFields()
    }
  }
)

// Expose resetFields for parent component
defineExpose({
  resetFields
})
</script>

<template>
  <menu style="margin-top: 1.5rem">
    <div class="row">
      <FloatLabel style="flex-grow: 1">
        <InputText
          v-model="add.mechanism"
          id="add-mechanism"
          :invalid="!!problem"
          aria-describedby="add-mechanism-hint"
          style="width: 100%"
        />
        <label for="add-mechanism">Mechanism*</label>
      </FloatLabel>
      <Button
        label="Add"
        icon="pi pi-plus"
        @click="
          () =>
            app.data.ionization.mechanism.create({
              ionization_mechanism: add.mechanism.trim()
            })
        "
        :disabled="!add.mechanism.trim() || !!problem"
      />
    </div>
    <Message
      id="add-mechanism-hint"
      :severity="problem ? 'error' : 'secondary'"
      size="small"
      variant="simple"
    >
      {{ hint }}
    </Message>
  </menu>
  <section style="margin: 1rem 0">
    <DataTable
      :value="app.data.ionization.mechanism.list"
      tableStyle="width: 500px"
      scrollable
      scrollHeight="calc(85vh - 260px)"
    >
      <Column field="ionization_mechanism_polarity" header="Polarity" width="2rem" sortable />
      <Column field="ionization_mechanism" header="Mechanism" width="40%" sortable />
      <Column field="ionization_mechanism_id" width="2rem">
        <template #body="{ data }">
          <Button
            v-tooltip="'Delete mechanism'"
            label="Delete mechanism"
            class="hiddenlabel"
            icon="pi pi-trash"
            text
            size="small"
            @click="
              () => {
                confirm.require({
                  icon: 'pi pi-exclamation-triangle',
                  header: `Delete ionization mechanism '${data.ionization_mechanism}'`,
                  message: `Are you sure you want to delete the ionization mechanism '${data.ionization_mechanism}'?`,
                  accept: () => {
                    app.data.ionization.mechanism.delete(data.ionization_mechanism_id)
                  },
                  acceptProps: {
                    icon: 'pi pi-trash',
                    label: 'Delete',
                    severity: 'danger'
                  },
                  rejectProps: {
                    label: 'Cancel',
                    severity: 'secondary'
                  }
                })
              }
            "
          />
        </template>
      </Column>
    </DataTable>
  </section>
</template>

<style scoped>
section :deep(*) {
  overflow-x: hidden !important;
}
</style>
