<script setup>
/**
 * Component for managing ionization mechanisms
 *
 * Allows adding and removing mechanisms with validation. A mechanism is typed
 * in the standard adduct notation (`[M+H]+`, `[M-H]-`, `[M]+.`); the legacy
 * spelling (`+H+`, `-H+`, `+`) is accepted too, and the server stores either
 * in the standard one. The mechanisms Mascope ships are on every server from
 * its first start and are not offered for deletion (`shipped` on the row).
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

// Under the examples: what is wrong, else the spelling the server will store
// when it is not what was typed (a legacy spelling, or terms in another order
// than the alphabetical one a mechanism is written in), else nothing.
const status = computed(() => {
  if (problem.value) return problem.value
  const text = add.mechanism.trim()
  const stored = text ? standardMechanism(text) : text
  return stored !== text ? `Stored as ${stored}` : ''
})

// The shipped mechanisms first, then the ones added on this server, each
// group in the order the server lists it.
const mechanisms = computed(() => {
  const list = app.data.ionization.mechanism.list
  return [...list.filter((row) => row.shipped), ...list.filter((row) => !row.shipped)]
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
  <!-- As wide as the table, so a long message wraps instead of widening the dialog -->
  <menu style="margin-top: 1.5rem; width: 500px; max-width: 100%">
    <div class="row">
      <FloatLabel style="flex-grow: 1">
        <InputText
          v-model="add.mechanism"
          id="add-mechanism"
          :invalid="!!problem"
          aria-describedby="add-mechanism-examples add-mechanism-status"
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
    <Message id="add-mechanism-examples" severity="secondary" size="small" variant="simple">
      For example [M+H]+, [M-H]-, [M+Br]-, or [M]+. for electron transfer
    </Message>
    <!-- Always a line tall, so the table does not move as the message comes and goes -->
    <Message
      id="add-mechanism-status"
      :severity="problem ? 'error' : 'secondary'"
      size="small"
      variant="simple"
    >
      <template v-if="status">{{ status }}</template>
      <template v-else>&nbsp;</template>
    </Message>
  </menu>
  <section style="margin: 1rem 0">
    <DataTable
      :value="mechanisms"
      tableStyle="width: 500px"
      scrollable
      scrollHeight="calc(85vh - 320px)"
    >
      <Column field="ionization_mechanism_polarity" header="Polarity" width="2rem" sortable />
      <Column field="ionization_mechanism" header="Mechanism" width="40%" sortable />
      <Column field="ionization_mechanism_id" width="2rem">
        <template #body="{ data }">
          <span
            v-if="data.shipped"
            class="locked"
            role="img"
            aria-label="Shipped with Mascope"
            v-tooltip="{
              value: 'Mascope ships this mechanism, so it cannot be deleted',
              showDelay: 500
            }"
          >
            <i class="pi pi-lock" />
          </span>
          <Button
            v-else
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

/* The box of the small text button beside it, so the lock and the trash line up */
.locked {
  display: inline-flex;
  vertical-align: bottom;
  padding: var(--p-button-sm-padding-y) var(--p-button-sm-padding-x);
  border: 1px solid transparent;
  opacity: 0.4;
}

.locked .pi {
  font-size: var(--p-button-sm-font-size);
}
</style>
