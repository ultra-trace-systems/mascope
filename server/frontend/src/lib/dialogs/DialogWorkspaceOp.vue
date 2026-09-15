<script setup>
import { ref, reactive, computed, watch } from 'vue'

import FloatLabel from 'primevue/floatlabel'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import Button from 'primevue/button'

import { useApp } from '@/stores'

const app = useApp()

const action = defineModel('action')

const props = defineProps({
  workspace: {
    type: Object
  }
})

const original = computed(() => {
  if (action.value == 'create') {
    return null
  }
  return props.workspace ?? app.data.workspace.focused
})

const info = reactive({
  name: null,
  desc: null,
  message: null,
  initial: null
})

const visible = ref(false)
watch(action, (value) => {
  visible.value = !!value
})
watch(visible, (value) => {
  if (!value) {
    action.value = null
  }
})

const title = computed(() => {
  const name = info.initial?.workspace_name ?? ''
  return {
    create: `Create a new workspace`,
    edit: `Edit workspace '${name}'`,
    delete: `Delete workspace '${name}'`
  }[action.value]
})

const executeLabel = computed(() => {
  return {
    create: `Create`,
    edit: `Save`,
    delete: `Delete`
  }[action.value]
})

async function execute() {
  switch (action.value) {
    case 'create': {
      const response = await app.data.workspace.create({
        workspace_name: info.name,
        workspace_description: info.desc
      })
      app.data.workspace.lazyFocus({
        workspace_id: response.data.workspace_id
      })
      break
    }
    case 'edit': {
      await app.data.workspace.update({
        workspace_id: original.value.workspace_id,
        workspace_name: info.name,
        workspace_description: info.desc
      })
      break
    }
    case 'delete': {
      if (app.data.workspace.list.length > 1) {
        await app.data.workspace.delete(original.value)
      } else {
        info.message =
          'You cannot delete the last remaining workspace. Create a new workspace before deleting this one.'
      }
      break
    }
  }
  if (!info.message) {
    action.value = null
  }
}

watch(action, init)
function init() {
  info.name = original.value?.workspace_name
  info.desc = original.value?.workspace_description
  ;((info.message = null), (info.initial = original.value))
}

const invalid = computed(() =>
  action.value == 'create' || action.value == 'edit' ? (info.name?.trim().length ?? 0) == 0 : false
)
</script>

<template>
  <Dialog v-model:visible="visible" :header="title" modal appendTo="body" style="width: 720px">
    <section>
      <template v-if="action !== 'delete'">
        <FloatLabel>
          <InputText id="workspace-name" v-model="info.name" style="width: 100%" />
          <label for="workspace-name">Name</label>
        </FloatLabel>
        <FloatLabel>
          <Textarea id="workspace-desc" v-model="info.desc" rows="4" style="width: 100%" />
          <label for="workspace-desc">Description</label>
        </FloatLabel>
      </template>
      <template v-else>
        <p v-if="info.message">{{ info.message }}</p>
        <template v-else>
          <p v-if="info.initial?.is_system" style="color: var(--state-error); font-weight: bold">
            Warning: This is a system workspace. Deleting it will remove all associated acquisition
            datasets and sample data. This action cannot be undone.
          </p>
          <p>Are you sure you want to delete the '{{ info.initial?.workspace_name }}' workspace?</p>
        </template>
      </template>
    </section>
    <menu>
      <Button label="Cancel" @click="action = null" severity="secondary" />
      <Button :label="executeLabel" @click="execute" v-if="!info.message" :disabled="invalid" />
    </menu>
  </Dialog>
</template>
