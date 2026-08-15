<script setup>
/**
 * Component for managing ionization modes
 *
 * Allows adding, editing and deleting ionization modes.
 */
import { reactive, computed, watch, ref } from 'vue'

import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'
import FloatLabel from 'primevue/floatlabel'
import Select from 'primevue/select'
import MultiSelect from 'primevue/multiselect'
import Fieldset from 'primevue/fieldset'
import { useConfirm } from 'primevue/useconfirm'
import { FilterMatchMode } from '@primevue/core/api'

import { useApp } from '@/stores'
import { ROLES } from '@/lib/roles'

const app = useApp()
const confirm = useConfirm()

// Editing or deleting a mode has a global effect on every sample processed
// under it, so those operations are restricted to admins. Creating stays at
// editor level (the create form remains visible to all editors).
const isAdmin = computed(() => app.auth.user.role_id >= ROLES.admin)

const add = reactive({
  ionization_mode_name: '',
  ionization_mode_token: '',
  ionization_mode_polarity: null,
  ionization_mechanism_ids: [],
  calibration_collection_id: null,
  diagnostic_collection_id: null
})

const edited = ref(null)

const resetFields = () => {
  add.ionization_mode_name = ''
  add.ionization_mode_token = ''
  add.ionization_mode_polarity = null
  add.ionization_mechanism_ids = []
  add.calibration_collection_id = null
  add.diagnostic_collection_id = null
}

const resetEdit = () => {
  edited.value = null
}

const editing = (data) => data.ionization_mode_id === edited.value?.ionization_mode_id

// Options
const polarityOptions = [
  { label: '+', value: '+' },
  { label: '-', value: '-' }
]

const calibrationCollections = computed(() =>
  app.data.target.collection.list.filter((c) => c.target_collection_type === 'CALIBRANTS')
)

const diagnosticCollections = computed(() =>
  app.data.target.collection.list.filter((c) => c.target_collection_type === 'DIAGNOSTICS')
)

// Filter mechanisms by selected polarity
const availableMechanisms = computed(() => {
  if (!add.ionization_mode_polarity) {
    return []
  }
  return app.data.ionization.mechanism.list.filter(
    (m) => m.ionization_mechanism_polarity === add.ionization_mode_polarity
  )
})

// Mode operations
// Mechanisms available for the currently edited mode's polarity
const editAvailableMechanisms = computed(() => {
  if (!edited.value?.ionization_mode_polarity) {
    return []
  }
  return app.data.ionization.mechanism.list.filter(
    (m) => m.ionization_mechanism_polarity === edited.value.ionization_mode_polarity
  )
})

const editHasChanges = computed(() => {
  if (!edited.value) return false
  const e = edited.value
  const o = e._original
  return (
    e.ionization_mode_name !== o.ionization_mode_name ||
    e.ionization_mode_token !== o.ionization_mode_token ||
    e.ionization_mode_polarity !== o.ionization_mode_polarity ||
    JSON.stringify([...e.ionization_mechanism_ids].sort()) !==
      JSON.stringify([...o.ionization_mechanism_ids].sort()) ||
    e.calibration_collection_id !== o.calibration_collection_id ||
    e.diagnostic_collection_id !== o.diagnostic_collection_id
  )
})

const mode = {
  edit: (data) => {
    edited.value = {
      ionization_mode_id: data.ionization_mode_id,
      ionization_mode_name: data.ionization_mode_name,
      ionization_mode_token: data.ionization_mode_token,
      ionization_mode_polarity: data.ionization_mode_polarity,
      ionization_mechanism_ids: [...data.ionization_mechanism_ids],
      calibration_collection_id: data.calibration_collection_id,
      diagnostic_collection_id: data.diagnostic_collection_id,
      // Store original values to detect changes
      _original: {
        ionization_mode_name: data.ionization_mode_name,
        ionization_mode_token: data.ionization_mode_token,
        ionization_mode_polarity: data.ionization_mode_polarity,
        ionization_mechanism_ids: [...data.ionization_mechanism_ids],
        calibration_collection_id: data.calibration_collection_id,
        diagnostic_collection_id: data.diagnostic_collection_id
      }
    }
  },
  cancel: resetEdit,
  save: async () => {
    if (!edited.value) return

    const e = edited.value
    const o = e._original
    const mechanismsChanged =
      JSON.stringify([...e.ionization_mechanism_ids].sort()) !==
      JSON.stringify([...o.ionization_mechanism_ids].sort())
    const calibrationChanged = e.calibration_collection_id !== o.calibration_collection_id
    const diagnosticChanged = e.diagnostic_collection_id !== o.diagnostic_collection_id

    const doSave = async () => {
      const { ionization_mode_id, _original, ...updateData } = edited.value
      await app.data.ionization.mode.update(ionization_mode_id, updateData)
      resetEdit()
    }

    // Editing a mode affects every sample processed under it. A calibration
    // collection change requires re-calibration to take effect (stronger
    // signal); mechanism/diagnostic changes only need a rematch. Warn
    // accordingly before persisting.
    if (calibrationChanged) {
      confirm.require({
        icon: 'pi pi-exclamation-triangle',
        header: 'Confirm ionization mode update',
        message:
          'Changing the calibration collection affects how all samples processed under ' +
          'this mode are mass-calibrated. Affected batches will be flagged for re-calibration ' +
          'and must be re-calibrated (then re-matched) for the new collection to take effect. ' +
          'Do you want to proceed?',
        accept: doSave,
        acceptProps: { icon: 'pi pi-check', label: 'Update', severity: 'warn' },
        rejectProps: { label: 'Cancel', severity: 'secondary' }
      })
    } else if (mechanismsChanged || diagnosticChanged) {
      confirm.require({
        icon: 'pi pi-exclamation-triangle',
        header: 'Confirm ionization mode update',
        message:
          'Changing the ionization mechanisms or diagnostic collection affects all associated ' +
          'samples. Batches containing those samples will be flagged for re-matching. ' +
          'Do you want to proceed?',
        accept: doSave,
        acceptProps: { icon: 'pi pi-check', label: 'Update', severity: 'warn' },
        rejectProps: { label: 'Cancel', severity: 'secondary' }
      })
    } else {
      await doSave()
    }
  },
  delete: (data) => {
    confirm.require({
      icon: 'pi pi-exclamation-triangle',
      header: `Delete ionization mode '${data.ionization_mode_name}'`,
      message: `Are you sure you want to delete the ionization mode '${data.ionization_mode_name}'?`,
      accept: () => {
        app.data.ionization.mode.delete(data.ionization_mode_id)
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
}

const filters = ref({
  global: { value: null, matchMode: FilterMatchMode.CONTAINS }
})

// Reset mechanisms when polarity changes
watch(
  () => add.ionization_mode_polarity,
  () => {
    add.ionization_mechanism_ids = []
  }
)

// Reset edited mechanisms when edited polarity changes
watch(
  () => edited.value?.ionization_mode_polarity,
  (newVal, oldVal) => {
    if (edited.value && oldVal && newVal !== oldVal) {
      edited.value.ionization_mechanism_ids = []
    }
  }
)

// Reset when create successful
watch(
  computed(() => app.data.ionization.mode.list.length),
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
  <Fieldset legend="Create New Ionization Mode">
    <menu class="row" style="gap: 0.5rem; flex-wrap: wrap; padding-top: 1rem">
      <FloatLabel style="flex-grow: 1; min-width: 200px">
        <InputText
          v-model="add.ionization_mode_name"
          id="add-mode-name"
          style="width: 100%"
          v-tooltip="{
            value: 'Enter a descriptive name for the ionization mode',
            showDelay: 500
          }"
        />
        <label for="add-mode-name">Mode Name*</label>
      </FloatLabel>

      <FloatLabel style="flex-grow: 1; min-width: 150px">
        <InputText
          v-model="add.ionization_mode_token"
          id="add-mode-token"
          style="width: 100%"
          v-tooltip="{
            value: 'Enter a unique token used in filenames to identify this mode',
            showDelay: 500
          }"
        />
        <label for="add-mode-token">Filename token</label>
      </FloatLabel>

      <FloatLabel style="flex-grow: 1; min-width: 120px">
        <Select
          v-model="add.ionization_mode_polarity"
          :options="polarityOptions"
          optionLabel="label"
          optionValue="value"
          id="add-polarity"
          style="width: 100%"
          v-tooltip="{ value: 'Select the ion polarity for this mode', showDelay: 500 }"
        />
        <label for="add-polarity">Polarity*</label>
      </FloatLabel>

      <FloatLabel style="flex-grow: 2; min-width: 200px">
        <MultiSelect
          v-model="add.ionization_mechanism_ids"
          :options="availableMechanisms"
          :disabled="!add.ionization_mode_polarity"
          :showToggleAll="false"
          optionLabel="ionization_mechanism"
          optionValue="ionization_mechanism_id"
          id="add-mechanisms"
          style="width: 100%"
          :placeholder="
            add.ionization_mode_polarity ? 'Select mechanisms' : 'Select polarity first'
          "
          v-tooltip="{
            value:
              'Choose one or more ionization mechanisms to apply for files acquired in this mode',
            showDelay: 500
          }"
        />
        <label for="add-mechanisms">Mechanisms*</label>
      </FloatLabel>

      <FloatLabel style="flex-grow: 1; min-width: 180px">
        <Select
          v-model="add.calibration_collection_id"
          :options="calibrationCollections"
          optionLabel="target_collection_name"
          optionValue="target_collection_id"
          id="add-calibration"
          style="width: 100%"
          v-tooltip="{
            value:
              'Select a target collection for this ionization mode to use for mass calibration',
            showDelay: 500
          }"
        />
        <label for="add-calibration">Calibration Collection</label>
      </FloatLabel>

      <FloatLabel style="flex-grow: 1; min-width: 180px">
        <Select
          v-model="add.diagnostic_collection_id"
          :options="diagnosticCollections"
          optionLabel="target_collection_name"
          optionValue="target_collection_id"
          id="add-diagnostic"
          style="width: 100%"
          v-tooltip="{
            value: 'Select a target collection for this ionization mode to use for quality control',
            showDelay: 500
          }"
        />
        <label for="add-diagnostic">Diagnostic Collection</label>
      </FloatLabel>

      <Button
        label="Create"
        icon="pi pi-plus"
        @click="
          () =>
            app.data.ionization.mode.create({
              ionization_mode_name: add.ionization_mode_name.trim(),
              ionization_mode_token: add.ionization_mode_token.trim() || null,
              ionization_mode_polarity: add.ionization_mode_polarity,
              ionization_mechanism_ids: add.ionization_mechanism_ids,
              calibration_collection_id: add.calibration_collection_id,
              diagnostic_collection_id: add.diagnostic_collection_id
            })
        "
        :disabled="
          !add.ionization_mode_name.trim() ||
          !add.ionization_mode_polarity ||
          !add.ionization_mechanism_ids.length
        "
        style="align-self: flex-end"
        v-tooltip="{ value: 'Add this ionization mode to the database', showDelay: 500 }"
      />
    </menu>
  </Fieldset>

  <section style="margin: 1rem 0">
    <DataTable
      :value="
        app.data.ionization.mode.list.filter(
          (im) => !im.ionization_mode_name.startsWith('AutoGenerated')
        )
      "
      v-model:filters="filters"
      :globalFilterFields="[
        'ionization_mode_name',
        'ionization_mode_token',
        'ionization_mode_polarity'
      ]"
      scrollable
      scrollHeight="calc(85vh - 350px)"
      tableStyle="min-width: 800px"
    >
      <Column header="Name" style="min-width: 150px">
        <template #body="{ data }">
          <InputText
            v-if="editing(data)"
            v-model="edited.ionization_mode_name"
            style="width: 100%"
          />
          <span v-else>{{ data.ionization_mode_name }}</span>
        </template>
      </Column>

      <Column header="Filename token" style="min-width: 120px">
        <template #body="{ data }">
          <InputText
            v-if="editing(data)"
            v-model="edited.ionization_mode_token"
            style="width: 100%"
          />
          <span v-else>{{ data.ionization_mode_token || '' }}</span>
        </template>
      </Column>

      <Column header="Polarity" style="width: 120px">
        <template #body="{ data }">
          <Select
            v-if="editing(data)"
            v-model="edited.ionization_mode_polarity"
            :options="polarityOptions"
            optionLabel="label"
            optionValue="value"
            style="width: 100%"
            v-tooltip="{
              value: 'Changing polarity will reset mechanism selections',
              showDelay: 500
            }"
          />
          <span v-else>{{ data.ionization_mode_polarity }}</span>
        </template>
      </Column>

      <Column header="Mechanisms" style="min-width: 200px">
        <template #body="{ data }">
          <MultiSelect
            v-if="editing(data)"
            v-model="edited.ionization_mechanism_ids"
            :options="editAvailableMechanisms"
            :showToggleAll="false"
            optionLabel="ionization_mechanism"
            optionValue="ionization_mechanism_id"
            style="width: 100%"
            placeholder="Select mechanisms"
            v-tooltip="{
              value: 'Choose one or more ionization mechanisms for this mode',
              showDelay: 500
            }"
          />
          <div v-else style="display: flex; flex-wrap: wrap; gap: 0.25rem">
            <span
              v-for="mechanismId in data.ionization_mechanism_ids"
              :key="mechanismId"
              class="mechanism-tag"
            >
              {{
                app.data.ionization.mechanism.list.find(
                  (m) => m.ionization_mechanism_id === mechanismId
                )?.ionization_mechanism || mechanismId
              }}
            </span>
          </div>
        </template>
      </Column>

      <Column header="Calibration Collection" style="min-width: 150px">
        <template #body="{ data }">
          <Select
            v-if="editing(data)"
            v-model="edited.calibration_collection_id"
            :options="calibrationCollections"
            optionLabel="target_collection_name"
            optionValue="target_collection_id"
            id="edit-calibration"
            style="width: 100%"
            v-tooltip="{
              value:
                'Select a target collection for this ionization mode to use for mass calibration',
              showDelay: 500
            }"
          />
          <span v-else>
            {{
              data.calibration_collection_id
                ? app.data.target.collection.list.find(
                    (c) => c.target_collection_id === data.calibration_collection_id
                  )?.target_collection_name || data.calibration_collection_id
                : ''
            }}
          </span>
        </template>
      </Column>

      <Column header="Diagnostic Collection" style="min-width: 150px">
        <template #body="{ data }">
          <Select
            v-if="editing(data)"
            v-model="edited.diagnostic_collection_id"
            :options="diagnosticCollections"
            optionLabel="target_collection_name"
            optionValue="target_collection_id"
            id="edit-diagnostic"
            style="width: 100%"
            v-tooltip="{
              value:
                'Select a target collection for this ionization mode to use for quality control',
              showDelay: 500
            }"
          />
          <span v-else>
            {{
              data.diagnostic_collection_id
                ? app.data.target.collection.list.find(
                    (c) => c.target_collection_id === data.diagnostic_collection_id
                  )?.target_collection_name || data.diagnostic_collection_id
                : ''
            }}
          </span>
        </template>
      </Column>

      <Column style="width: 120px">
        <template #body="{ data }">
          <div class="row" style="justify-content: flex-end; gap: 0.25rem">
            <template v-if="editing(data)">
              <Button
                v-tooltip="{ value: 'Cancel editing', showDelay: 500 }"
                icon="pi pi-times"
                text
                size="small"
                severity="secondary"
                @click="mode.cancel"
              />
              <Button
                v-tooltip="{ value: 'Save changes', showDelay: 500 }"
                icon="pi pi-check"
                text
                size="small"
                severity="secondary"
                :disabled="!edited?.ionization_mechanism_ids?.length || !editHasChanges"
                @click="mode.save"
              />
            </template>
            <template v-else-if="isAdmin">
              <Button
                v-tooltip="{ value: 'Edit ionization mode', showDelay: 500 }"
                icon="pi pi-pencil"
                text
                size="small"
                @click="mode.edit(data)"
              />
              <Button
                v-tooltip="{ value: 'Delete ionization mode', showDelay: 500 }"
                icon="pi pi-trash"
                text
                size="small"
                @click="mode.delete(data)"
              />
            </template>
            <template v-else>
              <i
                class="pi pi-lock"
                style="opacity: 0.4"
                v-tooltip="{ value: 'Editing ionization modes requires admin access', showDelay: 500 }"
              />
            </template>
          </div>
        </template>
      </Column>
    </DataTable>
  </section>
  <div class="row" style="padding-top: 1rem">
    <IconField>
      <InputIcon>
        <i class="pi pi-search" />
      </InputIcon>
      <InputText
        v-model="filters['global'].value"
        type="text"
        placeholder="Search ionization modes"
        style="width: 480px"
      />
    </IconField>
  </div>
</template>

<style scoped>
.row {
  display: flex;
  align-items: flex-start;
}

.mechanism-tag {
  background: var(--p-primary-100);
  color: var(--p-primary-900);
  padding: 0.25rem 0.5rem;
  border-radius: 0.375rem;
  font-size: 0.75rem;
  font-weight: 500;
}
</style>
