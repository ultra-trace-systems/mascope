<script setup>
/**
 * Choose which instrument's acquisitions are listed, or all of them.
 *
 * "All instruments" is the store's unfocused state rather than a record, so
 * it rides in the options as a sentinel and is mapped back to `unfocus()`.
 */
import { computed } from 'vue'

import Select from 'primevue/select'

import { useApp } from '@/stores'

const app = useApp()

const ALL_LABEL = 'All instruments'
const ALL = { instrument: null, all: true }

const options = computed(() => [ALL, ...(app.data.instrument.list ?? [])])

// A getter rather than the `instrument` field: the sentinel has no instrument
// name, and PrimeVue takes the option's aria-label and its type-ahead
// matching from this - a null label leaves the entry unnamed to a screen
// reader and unreachable by typing.
const labelOf = (option) => option?.instrument ?? ALL_LABEL

const chosen = computed({
  get: () => app.data.instrument.focused ?? ALL,
  set: (option) =>
    option?.instrument ? app.data.instrument.focus(option) : app.data.instrument.unfocus()
})
</script>

<template>
  <label for="instrument-selector" class="hidden">Instrument selector</label>
  <Select
    inputId="instrument-selector"
    v-model="chosen"
    :options="options"
    dataKey="instrument"
    :optionLabel="labelOf"
    optionDisabled="disabled"
    v-tooltip.left="'Instrument'"
    :pt="
      app.ui.help.bottom_end(`
          <h1>Instrument Selector</h1>

          <p>
            Select an instrument to list acquisitions for, or
            <em>All instruments</em> to list every one you can see at once.
            The Instrument column then says which is which.
          </p>
    `)
    "
  >
    <template #value="{ value }">
      <span v-if="value?.instrument">
        {{ value.instrument }}
      </span>
      <i v-else> {{ ALL_LABEL }} </i>
    </template>
    <template #option="{ option }">
      <span v-if="option.instrument">{{ option.instrument }}</span>
      <i v-else>{{ ALL_LABEL }}</i>
    </template>
    <template #dropdownicon>
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="20"
        height="20"
        fill="currentColor"
        viewBox="0 0 256 256"
      >
        <path
          d="M224,208H203.94A88.05,88.05,0,0,0,144,64.37V32a16,16,0,0,0-16-16H80A16,16,0,0,0,64,32V136a16,16,0,0,0,16,16h48a16,16,0,0,0,16-16V80.46A72,72,0,0,1,181.25,208H32a8,8,0,0,0,0,16H224a8,8,0,0,0,0-16Zm-96-72H80V32h48V136ZM72,184a8,8,0,0,1,0-16h64a8,8,0,0,1,0,16Z"
        ></path>
      </svg>
    </template>
  </Select>
</template>
