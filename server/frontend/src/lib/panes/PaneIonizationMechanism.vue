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

// The server's mechanisms in the standard notation, worked out once per list
// rather than once per keystroke.
const existing = computed(
  () =>
    new Set(
      app.data.ionization.mechanism.list.map((row) => standardMechanism(row.ionization_mechanism))
    )
)

// Why the typed mechanism cannot be added, or null: the notation first, then
// each term as a formula of element symbols, then whether the server already
// has it, compared in the standard notation so either spelling is caught. A
// labelled atom is written with a caret (^N); the bracketed form ([15N]) is
// refused here, as it always was, because target ions are built from the caret
// form alone. Whether the symbols are real elements is the server's check.
const problem = computed(() => {
  const text = add.mechanism.trim()
  if (!text) return null
  const notation = mechanismProblem(text)
  if (notation) return notation
  const term = mechanismTerms(text).find((term) => !isValidChemicalFormula(term))
  if (term) return `'${term}' is not a formula; a labelled atom is written with a caret, ^N`
  const stored = standardMechanism(text)
  return existing.value.has(stored) ? `${stored} is already a mechanism` : null
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
// group in the order the server lists it (the sort is stable).
const mechanisms = computed(() =>
  [...app.data.ionization.mechanism.list].sort((a, b) => Number(b.shipped) - Number(a.shipped))
)

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
  <!-- A column (a dialog's menu is a row), as wide as the table -->
  <menu class="add">
    <div class="row">
      <FloatLabel class="field">
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
    <!-- Exactly a line tall, so the table does not move as the message comes and
      goes; a message longer than the line is cut short, whole in its title -->
    <Message
      id="add-mechanism-status"
      class="status"
      :severity="problem ? 'error' : 'secondary'"
      size="small"
      variant="simple"
      :title="status || undefined"
    >
      <template v-if="status">{{ status }}</template>
      <template v-else>&nbsp;</template>
    </Message>
  </menu>
  <!-- The table takes the height left in the tab, scrolling its rows within it -->
  <section class="list">
    <DataTable :value="mechanisms" tableStyle="width: 500px" scrollable scrollHeight="flex">
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

.add {
  flex-flow: column;
  align-items: stretch;
  gap: 0;
  margin-top: 1.5rem;
  width: 500px;
  max-width: 100%;
}

/* The hints belong to the field, so keep them close under it rather than
   below a float label's usual bottom margin */
.field {
  flex-grow: 1;
  margin-bottom: 0.375rem;
}

/* A message is a grid, whose column would otherwise grow to the text */
.status {
  grid-template-columns: minmax(0, 1fr);
}

.status :deep(.p-message-text) {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

/* The table takes the height the form leaves in the panel, and scrolls its own
   rows. It keeps its header and a row, though: on a screen too short for that,
   the panel scrolls instead. */
.list {
  flex: 1;
  min-height: 7rem;
  display: flex;
  flex-flow: column;
  margin: 1rem 0;
}

.list :deep(.p-datatable) {
  flex: 1;
  min-height: 0;
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
