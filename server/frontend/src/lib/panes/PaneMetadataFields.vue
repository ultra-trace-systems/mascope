<script setup>
/**
 * The metadata field registry: the typed fields acquisition metadata is
 * written under, one registry for the whole deployment. Lists every field,
 * and lets the global editor role or higher add, change, retire, restore and
 * delete one (docs/authorization.md, shared reference data).
 *
 * The server keeps a field's meaning: its key never changes, a field in use
 * is retired rather than deleted and keeps its type, and a field Mascope
 * ships keeps its key, type and allowed values. The pane disables what the
 * server would refuse, and shows the server's reason when it refuses anyway.
 */
import { computed, reactive, ref, watch } from 'vue'

import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'
import ToggleSwitch from 'primevue/toggleswitch'

import { getApiErrorMessage } from '@/api/utils'
// From their files, not the base index: the index loads components that read
// the runtime config on import, which this pane (and its spec) need not pull in.
import BaseEmptyState from '@/lib/base/BaseEmptyState.vue'
import BaseLoadError from '@/lib/base/BaseLoadError.vue'
import {
  CHOICE_TYPES,
  FIELD_TYPES,
  allowedValuesLabel,
  deleteBlocked,
  fieldBody,
  fieldForm,
  fieldTypeLabel,
  formProblem,
  keyFromLabel
} from '@/lib/metadataFields'
import { ROLES } from '@/lib/roles'
import { useAuth } from '@/stores/auth'
import { useMetadataField } from '@/stores/data/modules/metadataField'

const auth = useAuth()
const registry = useMetadataField()

const canEdit = computed(() => (auth.user?.role_id ?? 0) >= ROLES.editor)
const showRetired = ref(false)

const rows = computed(() =>
  showRetired.value ? registry.list : registry.list.filter((field) => !field.retired)
)
const retiredCount = computed(() => registry.list.filter((field) => field.retired).length)

// The form: null when closed, else the field being changed (or null for a
// new one) under `original`.
const editing = ref(null)
const form = reactive(fieldForm())
const keyTouched = ref(false)
const error = ref(null)
const busy = ref(false)
// The field whose Delete was clicked once: a second click deletes it.
const confirmDelete = ref(null)

const creating = computed(() => editing.value && !editing.value.original)
const original = computed(() => editing.value?.original ?? null)
const typeFixed = computed(() => !!original.value?.system || !!original.value?.in_use)
const valuesFixed = computed(() => !!original.value?.system)
const problem = computed(() => (editing.value ? formProblem(form, creating.value) : null))

const open = (field = null) => {
  Object.assign(form, fieldForm(field))
  keyTouched.value = false
  error.value = null
  editing.value = { original: field }
}

const close = () => {
  editing.value = null
  error.value = null
}

// Suggest the key from the label until the key is typed by hand.
watch(
  () => form.label,
  (label) => {
    if (creating.value && !keyTouched.value) form.key = keyFromLabel(label)
  }
)

const run = async (action) => {
  busy.value = true
  error.value = null
  try {
    await action()
    return true
  } catch (e) {
    error.value = getApiErrorMessage(e, 'The registry did not take the change.')
    return false
  } finally {
    busy.value = false
  }
}

const save = async () => {
  if (problem.value) return
  const body = fieldBody(form, original.value)
  const saved = await run(async () => {
    if (creating.value) await registry.create(body)
    else if (Object.keys(body).length) await registry.update(original.value.metadata_field_id, body)
  })
  if (saved) {
    close()
    registry.load('saved')
  }
}

const setRetired = async (field, retired) => {
  if (await run(() => registry.update(field.metadata_field_id, { retired }))) {
    registry.load(retired ? 'retired' : 'restored')
  }
}

const remove = async (field) => {
  if (confirmDelete.value !== field.metadata_field_id) {
    confirmDelete.value = field.metadata_field_id
    return
  }
  confirmDelete.value = null
  if (await run(() => registry.delete(field.metadata_field_id))) {
    if (original.value?.metadata_field_id === field.metadata_field_id) close()
    registry.load('deleted')
  }
}

defineExpose({ resetFields: close })
</script>

<template>
  <section class="pane-metadata-fields">
    <p class="intro">
      The typed fields acquisition metadata is recorded under, shared by every instrument and
      workspace. A field in use is retired, not deleted.
    </p>
    <menu class="toolbar">
      <div class="toggle">
        <ToggleSwitch v-model="showRetired" inputId="metadata-fields-show-retired" />
        <label for="metadata-fields-show-retired">
          Show retired<span v-if="retiredCount"> ({{ retiredCount }})</span>
        </label>
      </div>
      <Button
        v-if="canEdit"
        label="Add field"
        icon="pi pi-plus"
        size="small"
        :disabled="busy"
        @click="open()"
      />
    </menu>

    <Message v-if="error && !editing" severity="error" class="error" closable>
      {{ error }}
    </Message>

    <BaseLoadError
      v-if="registry.error"
      :error="registry.error"
      fallback="Could not load the metadata fields."
      :onRetry="() => registry.load('retry')"
    />
    <BaseEmptyState
      v-else-if="!rows.length && !registry.pending"
      icon="pi ph ph-tag"
      title="No metadata fields"
      :hint="canEdit ? 'Add one to record it for the samples of every instrument.' : ''"
    />
    <DataTable
      v-else
      :value="rows"
      dataKey="metadata_field_id"
      :loading="registry.pending"
      scrollable
      scrollHeight="flex"
      size="small"
      class="table"
    >
      <Column header="Field">
        <template #body="{ data }">
          <div class="label">{{ data.label }}</div>
          <div class="key">{{ data.key }}</div>
        </template>
      </Column>
      <Column header="Type">
        <template #body="{ data }">{{ fieldTypeLabel(data.type) }}</template>
      </Column>
      <Column header="Unit">
        <template #body="{ data }">{{ data.unit ?? '' }}</template>
      </Column>
      <Column header="Allowed values">
        <template #body="{ data }">{{ allowedValuesLabel(data) }}</template>
      </Column>
      <Column header="Description">
        <template #body="{ data }"
          ><span class="description">{{ data.description ?? '' }}</span></template
        >
      </Column>
      <Column header="">
        <template #body="{ data }">
          <div class="tags">
            <Tag v-if="data.system" value="Mascope" severity="info" />
            <Tag v-if="data.in_use" value="In use" severity="secondary" />
            <Tag v-if="data.retired" value="Retired" severity="warn" />
          </div>
        </template>
      </Column>
      <Column v-if="canEdit" header="" class="actions">
        <template #body="{ data }">
          <div class="row-actions">
            <Button
              icon="pi pi-pencil"
              text
              size="small"
              :aria-label="`Edit ${data.label}`"
              v-tooltip.left="'Edit'"
              :disabled="busy"
              @click="open(data)"
            />
            <Button
              v-if="!data.system"
              :icon="data.retired ? 'pi pi-replay' : 'pi ph ph-archive'"
              text
              size="small"
              :aria-label="data.retired ? `Restore ${data.label}` : `Retire ${data.label}`"
              v-tooltip.left="data.retired ? 'Restore' : 'Retire'"
              :disabled="busy"
              @click="setRetired(data, !data.retired)"
            />
            <Button
              :icon="confirmDelete === data.metadata_field_id ? 'pi pi-check' : 'pi pi-trash'"
              text
              size="small"
              severity="danger"
              :aria-label="
                confirmDelete === data.metadata_field_id
                  ? `Confirm delete ${data.label}`
                  : `Delete ${data.label}`
              "
              v-tooltip.left="
                deleteBlocked(data) ??
                (confirmDelete === data.metadata_field_id ? 'Click again to delete' : 'Delete')
              "
              :disabled="busy || !!deleteBlocked(data)"
              @click="remove(data)"
            />
          </div>
        </template>
      </Column>
    </DataTable>

    <form v-if="editing" class="field-form" @submit.prevent="save">
      <h3>{{ creating ? 'New field' : `Edit ${original.label}` }}</h3>
      <div class="grid">
        <label for="metadata-field-label">Label</label>
        <InputText id="metadata-field-label" v-model="form.label" maxlength="128" />

        <label for="metadata-field-key">Key</label>
        <div>
          <InputText
            id="metadata-field-key"
            v-model="form.key"
            maxlength="64"
            :disabled="!creating"
            @input="keyTouched = true"
          />
          <small class="hint">
            {{
              creating
                ? 'The name values are stored under. It cannot be changed later.'
                : 'A key never changes.'
            }}
          </small>
        </div>

        <label for="metadata-field-type">Type</label>
        <div>
          <Select
            inputId="metadata-field-type"
            v-model="form.type"
            :options="FIELD_TYPES"
            optionLabel="label"
            optionValue="value"
            :disabled="typeFixed"
          />
          <small v-if="original?.system" class="hint">Mascope ships this field.</small>
          <small v-else-if="original?.in_use" class="hint"> In use, so its type is fixed. </small>
        </div>

        <template v-if="form.type === 'number'">
          <label for="metadata-field-unit">Unit</label>
          <InputText id="metadata-field-unit" v-model="form.unit" maxlength="32" />
        </template>

        <template v-if="CHOICE_TYPES.has(form.type)">
          <label for="metadata-field-values">Allowed values</label>
          <div>
            <Textarea
              id="metadata-field-values"
              v-model="form.allowedValues"
              rows="4"
              autoResize
              :disabled="valuesFixed"
            />
            <small class="hint">
              {{
                valuesFixed
                  ? 'Filled by Mascope.'
                  : form.type === 'tags'
                    ? 'One per line. Leave empty to allow any tag.'
                    : 'One per line.'
              }}
            </small>
          </div>
        </template>

        <label for="metadata-field-description">Description</label>
        <Textarea id="metadata-field-description" v-model="form.description" rows="2" autoResize />
      </div>
      <Message v-if="error" severity="error" class="error">{{ error }}</Message>
      <Message v-else-if="problem" severity="secondary" class="error">{{ problem }}</Message>
      <menu class="form-actions">
        <Button label="Cancel" severity="secondary" text :disabled="busy" @click="close" />
        <Button
          type="submit"
          :label="creating ? 'Add' : 'Save'"
          icon="pi pi-check"
          :disabled="busy || !!problem"
        />
      </menu>
    </form>
  </section>
</template>

<style scoped>
.pane-metadata-fields {
  display: flex;
  flex-flow: column;
  gap: 0.75rem;
  min-height: 0;
  flex: 1;
}

.intro {
  margin: 0;
  opacity: 0.7;
}

.toolbar,
.form-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0;
  margin: 0;
}

.form-actions {
  justify-content: flex-end;
}

.toggle {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.table {
  flex: 1;
  min-height: 12rem;
}

.label {
  font-weight: 600;
}

.key {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.85rem;
  opacity: 0.6;
}

.description {
  font-size: 0.9rem;
  opacity: 0.8;
}

.tags,
.row-actions {
  display: flex;
  gap: 0.25rem;
  justify-content: flex-end;
  white-space: nowrap;
}

.field-form {
  border-top: 1px solid var(--p-content-border-color);
  padding-top: 0.75rem;

  h3 {
    margin: 0 0 0.75rem;
  }
}

.grid {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 0.6rem 1rem;
  align-items: baseline;

  .p-inputtext,
  .p-select,
  .p-textarea {
    width: 100%;
  }
}

.hint {
  display: block;
  opacity: 0.6;
  margin-top: 0.2rem;
}

.error {
  margin: 0.5rem 0;
}
</style>
