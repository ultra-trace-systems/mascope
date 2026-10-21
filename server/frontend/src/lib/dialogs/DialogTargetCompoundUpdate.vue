<script setup>
import Dialog from 'primevue/dialog'
import FloatLabel from 'primevue/floatlabel'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import Panel from 'primevue/panel'

import { computed, reactive, ref, watch } from 'vue'

import { compoundIdentity, identityChanges, isValidInchi, isValidInchikey } from '@/lib/chem'
import { useApp } from '@/stores'

const app = useApp()

const props = defineProps({
  compound: {
    type: Object
  }
})

const visible = defineModel('visible')

const input = reactive({
  target_compound_id: null,
  target_compound_formula: null,
  target_compound_name: null,
  cas_number: null,
  inchikey: null,
  inchi: null,
  smiles: null
})

// The dialog opens on a match record, which carries neither the CAS number
// nor the structure identity: the stored compound does. The identity the
// inputs start from is kept, so Save sends only the identity fields the user
// changed - never an empty one that would clear what is stored.
const listed = computed(() =>
  app.data.target.compound.list?.find(
    ({ target_compound_id }) => target_compound_id === props.compound?.target_compound_id
  )
)
const initialIdentity = ref(compoundIdentity(null))
const initialCasNumber = ref(null)
const identityFromList = ref(false)
// The Structure group starts open only when it has something to show.
const structureCollapsed = ref(true)

const seedStructure = (source) => {
  initialCasNumber.value = source?.cas_number
  input.cas_number = initialCasNumber.value
  initialIdentity.value = compoundIdentity(source)
  Object.assign(input, initialIdentity.value)
  structureCollapsed.value = ![input.cas_number, ...Object.values(initialIdentity.value)].some(
    Boolean
  )
}

const structureEdited = () =>
  input.cas_number !== initialCasNumber.value ||
  Object.keys(identityChanges(initialIdentity.value, input)).length > 0

// The inputs are filled once per opened compound, not on every change of the
// compound list: a list sync while the dialog is open (anyone's edit, a
// socket reload) must not reset what the user is typing.
watch(
  [() => props.compound?.target_compound_id, visible],
  () => {
    input.target_compound_id = props.compound?.target_compound_id
    input.target_compound_formula = props.compound?.target_compound_formula
    input.target_compound_name = props.compound?.target_compound_name
    identityFromList.value = Boolean(listed.value)
    seedStructure(listed.value ?? props.compound)
  },
  { immediate: true }
)

// A list that loads only after the dialog opened gives the stored CAS number
// and identity then, once, unless the user has already changed one of them.
watch(listed, (compound) => {
  if (!compound || identityFromList.value) return
  identityFromList.value = true
  if (structureEdited()) return
  seedStructure(compound)
})

const invalidInchikey = computed(() => !isValidInchikey(input.inchikey))
const invalidInchi = computed(() => !isValidInchi(input.inchi))

const save = () => {
  const { inchikey, inchi, smiles, ...fields } = input
  app.data.target.compound.update({
    ...fields,
    ...identityChanges(initialIdentity.value, input)
  })
  visible.value = false
}
</script>

<template>
  <Dialog
    v-model:visible="visible"
    :header="`Edit target compound '${compound?.target_compound_formula}'`"
  >
    <div class="col" style="gap: 0rem">
      <FloatLabel style="margin: 1rem 0; margin-top: 1.5rem">
        <InputText
          id="add-compound-formula"
          required
          v-model="input.target_compound_formula"
          disabled
        />
        <label for="add-compound-formula"> Formula </label>
      </FloatLabel>
      <FloatLabel style="margin: 1rem 0">
        <InputText id="add-compound-name" required v-model="input.target_compound_name" />
        <label for="add-compound-name"> Name </label>
      </FloatLabel>
      <Panel
        header="Structure"
        toggleable
        v-model:collapsed="structureCollapsed"
        style="width: 100%; margin: 0.5rem 0 1rem 0"
      >
        <div class="col" style="gap: 0rem">
          <p style="margin: 0; max-width: 22rem; font-size: 0.875rem">
            What this target stands for. A match on its formula does not confirm the structure.
          </p>
          <FloatLabel style="margin: 1rem 0; margin-top: 1.5rem">
            <InputText id="add-compound-cas" v-model="input.cas_number" />
            <label for="add-compound-cas"> CAS </label>
          </FloatLabel>
          <FloatLabel style="margin: 1rem 0">
            <InputText
              id="edit-compound-inchikey"
              v-model="input.inchikey"
              :invalid="invalidInchikey"
            />
            <label for="edit-compound-inchikey"> InChIKey </label>
          </FloatLabel>
          <FloatLabel style="margin: 1rem 0">
            <InputText id="edit-compound-inchi" v-model="input.inchi" :invalid="invalidInchi" />
            <label for="edit-compound-inchi"> InChI </label>
          </FloatLabel>
          <FloatLabel style="margin: 1rem 0 0 0">
            <InputText id="edit-compound-smiles" v-model="input.smiles" />
            <label for="edit-compound-smiles"> SMILES </label>
          </FloatLabel>
        </div>
      </Panel>
      <menu style="justify-content: flex-end; width: 100%">
        <Button
          label="Cancel"
          severity="secondary"
          icon="pi pi-times"
          @click="
            () => {
              visible = false
            }
          "
        />
        <Button
          label="Save"
          icon="pi pi-save"
          :disabled="invalidInchikey || invalidInchi"
          v-tooltip="
            invalidInchikey
              ? 'An InChIKey reads like WQZGKKKJIJFFOK-GASJEMHNSA-N'
              : invalidInchi
                ? 'An InChI starts with InChI='
                : undefined
          "
          @click="save"
        />
      </menu>
    </div>
  </Dialog>
</template>
