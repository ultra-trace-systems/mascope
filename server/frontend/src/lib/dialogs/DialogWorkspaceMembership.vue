<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'

import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Select from 'primevue/select'
import { useConfirm } from 'primevue/useconfirm'

import { api } from '@/api'
import { useApp } from '@/stores'
import { workspaceRoles, prettyWorkspaceRoleName, roleLevel } from '@/lib/roles'

const props = defineProps({
  workspace: { type: Object, default: null }
})

const app = useApp()
const confirm = useConfirm()

const visible = defineModel('visible')

const close = () => {
  visible.value = false
}

const members = ref([])
const loading = ref(false)
const edited = ref(null)
const created = ref(null)

const reset = () => {
  edited.value = null
  created.value = null
}

const editing = ({ user_id }) => user_id === edited.value?.user_id

const loadMembers = async () => {
  if (!props.workspace?.workspace_id) return
  loading.value = true
  try {
    const result = await app.data.workspace.getMembers(props.workspace.workspace_id)
    members.value = result.data ?? result ?? []
  } finally {
    loading.value = false
  }
}

// Enrich members with display data
const enrichedMembers = computed(() =>
  members.value.map((m) => {
    return {
      ...m,
      username: m.username ?? `User #${m.user_id}`,
      pretty_role: prettyWorkspaceRoleName(m.workspace_role)
    }
  })
)

// Users not yet members of this workspace
const availableUsers = computed(() => {
  const memberIds = new Set(members.value.map((m) => m.user_id))
  return app.data.user.list.filter((u) => !memberIds.has(u.id))
})

const isMe = (member) => member.user_id === app.auth.user.id

// Workspace role the current user holds in this workspace
const myMembership = computed(() => members.value.find((m) => m.user_id === app.auth.user.id))
const myRoleLevel = computed(() => {
  const role = myMembership.value?.workspace_role
  return workspaceRoles.find(({ value }) => value === role)?.level ?? 0
})

const canModify = (workspace_role) => {
  const targetLevel = workspaceRoles.find(({ value }) => value === workspace_role)?.level ?? 0
  // Global superusers can always modify
  if (app.auth.user.role_name === 'owner') return true
  // Workspace owners can modify anyone (including other owners)
  if (myRoleLevel.value >= roleLevel('owner')) return targetLevel <= myRoleLevel.value
  // Workspace admins can modify members below their level
  return myRoleLevel.value >= roleLevel('admin') && targetLevel < myRoleLevel.value
}

const selectableRoles = computed(() =>
  workspaceRoles.filter(({ level }) => {
    if (app.auth.user.role_name === 'owner') return true
    // Workspace owners can assign any role including owner
    if (myRoleLevel.value >= roleLevel('owner')) return level <= myRoleLevel.value
    // Workspace admins can assign up to their own level
    return myRoleLevel.value >= roleLevel('admin') && level <= myRoleLevel.value
  })
)

const member = {
  edit: ({ user_id, workspace_role }) => {
    created.value = null
    edited.value = { user_id, workspace_role }
  },
  create: () => {
    edited.value = null
    created.value = { user_id: null, workspace_role: 'guest' }
  },
  cancel: reset,
  save: async () => {
    const wid = props.workspace.workspace_id
    if (edited.value) {
      await app.data.workspace.updateMember(wid, edited.value.user_id, {
        workspace_role: edited.value.workspace_role
      })
    }
    if (created.value && created.value.user_id) {
      await app.data.workspace.addMember(wid, {
        user_id: created.value.user_id,
        workspace_role: created.value.workspace_role
      })
    }
    reset()
    await loadMembers()
  },
  delete: (data) => {
    const self = isMe(data)
    confirm.require({
      icon: 'pi pi-exclamation-triangle',
      header: self ? 'Leave workspace' : 'Remove member',
      message: self
        ? `Are you sure you want to leave this workspace?`
        : `Are you sure you want to remove ${data.username} from this workspace?`,
      accept: async () => {
        await app.data.workspace.removeMember(props.workspace.workspace_id, data.user_id)
        if (self) {
          close()
          await app.data.workspace.sync()
        } else {
          await loadMembers()
        }
      },
      acceptProps: {
        icon: self ? 'pi pi-sign-out' : 'pi pi-trash',
        label: self ? 'Leave' : 'Remove',
        severity: 'danger'
      },
      rejectProps: {
        icon: 'pi pi-times',
        label: 'Cancel',
        severity: 'secondary'
      }
    })
  }
}

const invalidCreated = computed(() => {
  return !created.value?.user_id
})

// A membership can change while this dialog sits open: another administrator
// editing the same workspace, or an account being registered, which enrols the
// new account in every system workspace. Both go through the member
// controller, which announces the change on the workspace record-reload
// channel - so listen for it and refresh the roster in place.
const onWorkspaceReload = ({ record_id } = {}) => {
  // The broadcast reaches every room this tab is subscribed to, and the
  // list-level ones (a workspace created or deleted) name no workspace at all.
  // Only the ones naming the workspace on screen say anything about this list.
  if (!record_id || record_id !== props.workspace?.workspace_id) return
  loadMembers()
}

// The announcement only reaches rooms this tab has joined, and the workspace
// store joins just the focused one. The dialog can be opened on another: the
// workspace pane's context menu passes the right-clicked workspace, which need
// not be the selected one. So the dialog joins its own workspace's room for as
// long as it needs it.
//
// The tab holds one subscription per room and counts no references (the socket
// keeps a Set, and the server does a plain leave_room), so the store and this
// dialog can end up needing the same room. Focus can move onto the workspace
// on screen while the dialog sits open, and the store re-joins only when focus
// changes - so a leave here would take the store's live updates down with it
// and the store would never learn. The invariant is therefore two-sided:
//
//   the dialog joins a room only when the store does not already hold it, and
//   leaves a room only if it joined that room itself AND the store does not
//   hold it by the time the dialog lets go.
//
// The mirror failure - hoarding rooms - cannot follow: a room kept back on
// close is by definition the focused one, which the store now owns and drops
// on its next focus change.
const joinedRoom = ref(null)

const claimRoom = (room) => {
  if (!room || room === app.data.workspace.focusedId) return
  api.socket.addSubscription(room)
  joinedRoom.value = room
}

const releaseRoom = () => {
  const room = joinedRoom.value
  if (!room) return
  joinedRoom.value = null
  if (room === app.data.workspace.focusedId) return
  api.socket.removeSubscription(room)
}

// The workspace this dialog is showing, and nothing once it closes. Driving
// the room off this rather than off `visible` alone keeps the joined room and
// the roster from drifting away from the workspace on screen, should the
// dialog ever be rebound to another workspace without being closed first.
const shownWorkspaceId = computed(() =>
  visible.value ? (props.workspace?.workspace_id ?? null) : null
)

watch(shownWorkspaceId, (id) => {
  releaseRoom()
  claimRoom(id)
  if (id) loadMembers()
})

const teardown = () => {
  api.socket.off('workspace_reload', onWorkspaceReload)
  releaseRoom()
}

watch(visible, (v) => {
  reset()
  if (v) {
    api.socket.on('workspace_reload', onWorkspaceReload)
  } else {
    teardown()
  }
})

// The dialog is hidden rather than unmounted, so the close branch above is the
// usual teardown; this catches the view being torn down with it still open.
onUnmounted(teardown)
</script>

<template>
  <Dialog
    v-model:visible="visible"
    :header="`Members of ${workspace?.workspace_name ?? 'Workspace'}`"
    modal
    appendTo="body"
    style="width: 720px"
    closable
  >
    <section>
      <DataTable
        :value="enrichedMembers"
        :loading="loading"
        sortField="workspace_role"
        :sortOrder="-1"
        scrollable
        scrollHeight="400px"
      >
        <Column header="Username" field="username">
          <template #body="{ data }">
            {{ data.username }}
          </template>
        </Column>
        <Column header="Role" field="workspace_role">
          <template #body="{ data }">
            <Select
              v-if="editing(data)"
              v-model:modelValue="edited.workspace_role"
              :options="selectableRoles"
              optionLabel="label"
              optionValue="value"
              :disabled="isMe(data)"
            />
            <span v-else>{{ data.pretty_role }}</span>
          </template>
        </Column>
        <Column
          header="Actions"
          headerStyle="display: flex; justify-content: flex-end; padding-right: 2rem"
        >
          <template #body="{ data }">
            <div class="row" style="justify-content: flex-end; min-width: 50px">
              <template v-if="editing(data)">
                <Button
                  v-tooltip.bottom="'Cancel'"
                  icon="pi pi-times"
                  severity="secondary"
                  text
                  @click="member.cancel"
                />
                <Button
                  v-tooltip.bottom="'Save'"
                  icon="pi pi-check"
                  severity="secondary"
                  text
                  @click="member.save"
                />
              </template>
              <template v-else>
                <Button
                  v-tooltip.bottom="isMe(data) ? 'Leave workspace' : 'Remove member'"
                  :icon="isMe(data) ? 'pi pi-sign-out' : 'pi pi-user-minus'"
                  severity="secondary"
                  text
                  @click="member.delete(data)"
                  :disabled="!isMe(data) && !canModify(data.workspace_role)"
                />
                <Button
                  v-tooltip.bottom="'Edit role'"
                  icon="pi pi-user-edit"
                  severity="secondary"
                  text
                  @click="member.edit(data)"
                  :disabled="!canModify(data.workspace_role) || isMe(data)"
                />
              </template>
            </div>
          </template>
        </Column>
      </DataTable>
      <template v-if="created">
        <menu
          class="row"
          style="justify-content: space-between; margin-top: 2rem; padding: 0 0.5rem"
        >
          <span class="pi pi-user-plus" />
          <Select
            v-model:modelValue="created.user_id"
            :options="availableUsers"
            optionLabel="username"
            optionValue="id"
            placeholder="Select user"
            filter
            style="min-width: 200px"
          />
          <Select
            v-model:modelValue="created.workspace_role"
            :options="selectableRoles"
            optionLabel="label"
            optionValue="value"
          />
          <menu class="row" style="padding: 0 0.5rem">
            <Button
              icon="pi pi-times"
              text
              v-tooltip.bottom="'Cancel'"
              @click="reset"
              severity="secondary"
            />
            <Button
              icon="pi pi-save"
              text
              v-tooltip.bottom="'Add member'"
              @click="member.save"
              severity="secondary"
              :disabled="invalidCreated"
            />
          </menu>
        </menu>
      </template>
    </section>
    <menu style="justify-content: space-between; margin-top: 3rem">
      <Button
        icon="pi pi-user-plus"
        label="Add member"
        @click="member.create"
        :disabled="created || availableUsers.length === 0"
      />
      <Button icon="pi pi-times" label="Close" @click="close" severity="secondary" />
    </menu>
  </Dialog>
</template>
