<script setup>
import FloatLabel from 'primevue/floatlabel'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'

import { reactive, computed, watch } from 'vue'

import { genId } from '@/lib/utils'
import { useApp } from '@/stores'

const app = useApp()

const props = defineProps({
  original: {
    type: Object,
    required: true
  }
})

const visible = defineModel('visible')

// The registry fields the template selects travel with the copy, but for a
// retired one: the server adds no retired field to a new template.
const copiedFields = () => {
  const retired = new Set(
    app.data.metadataField.list.filter((field) => field.retired).map((field) => field.key)
  )
  return (props.original.fields ?? []).filter((key) => !retired.has(key))
}

const updated = reactive({
  name: 'new template',
  type: props.original.type,
  template: props.original.template.map((field) => ({ key: genId(8, false), ...field })),
  fields: copiedFields()
})
const title = computed(() => `Create new template based on '${props.original.name}'`)

const addField = () => {
  updated.template.push({
    key: genId(8, false),
    label: 'new field',
    value: '',
    placeholder: 'field label',
    maxlength: 100
  })
}
const removeField = (label) => {
  updated.template = updated.template.filter((f) => f.label !== label)
}

watch(visible, init)
function init(active) {
  if (!active) return
  updated.name = `${props.original.name} (modified)`
  updated.type = props.original.type
  updated.template = props.original.template.map((field) => ({
    key: genId(8, false),
    ...field
  }))
  updated.fields = copiedFields()
}
</script>

<template>
  <Dialog :header="title" v-model:visible="visible">
    <FloatLabel>
      <InputText id="template-created-name" v-model="updated.name" />
      <label for="template-created-name"> Template Name </label>
    </FloatLabel>

    <div class="editable-field" v-for="field in updated.template" :key="field.key">
      <FloatLabel>
        <InputText
          id="item-field-label"
          v-model="field.label"
          :required="field.required"
          :disabled="field.disabled"
        />
        <label for="item-field-label"> Field Label </label>
      </FloatLabel>
      <Button
        :id="field.label"
        :disabled="field.required"
        @click="() => removeField(field.label)"
        icon="pi pi-trash"
        severity="danger"
      />
    </div>
    <div class="editable-field" style="justify-content: flex-end">
      <Button label="Add field" @click="() => addField()" icon="pi pi-plus" />
    </div>
    <menu>
      <Button label="Cancel" @click="() => (visible = false)" severity="secondary" />
      <Button
        label="Create"
        @click="
          async () => {
            await app.data.template.create(updated)
            visible = false
          }
        "
      />
    </menu>
  </Dialog>
</template>

<style scoped>
.template-menu {
  padding: 0;
  margin: 0;
  gap: 0.5rem;
  display: flex;
  flex-flow: row nowrap;
  align-items: baseline;
  justify-content: flex-end;
}
.template-menu :deep(*) {
  margin: 0;
}
.editable-field {
  display: flex;
  flex-flow: row nowrap;
  align-items: baseline;
  gap: 0.5rem;
  margin: 1rem 0;
}
.editable-field :deep(.p-floatlabel) {
  margin-bottom: 0;
}
</style>
