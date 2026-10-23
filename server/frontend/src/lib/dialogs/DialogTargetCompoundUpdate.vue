<script setup>
import Dialog from 'primevue/dialog'
import FloatLabel from 'primevue/floatlabel'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import Panel from 'primevue/panel'
import Tag from 'primevue/tag'

import { computed, reactive, ref, watch } from 'vue'

import { getApiErrorMessage } from '@/api/utils'
import { compoundIdentity, identityChanges, isValidInchi, isValidInchikey } from '@/lib/chem'
import { useApp } from '@/stores'

// Reference rows per page of Find structure.
const STRUCTURES_PAGE = 10

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

// Find structure: the reference rows of the compound's formula, the ones of
// its CAS number and name first. Picking one fills the Structure group, which
// Save then sends like any typed identity, so the API still checks the InChI
// against the formula.
const lookup = reactive({
  // The page being read, null when none is.
  loadingPage: null,
  // What the first page asked, which the later pages ask again: the order
  // follows the CAS number and the name, so a page read after either changed
  // would repeat or skip rows.
  query: null,
  // The rows read so far, page by page; null before a lookup.
  rows: null,
  // How many rows the lookup has, on every page.
  total: 0,
  page: 0,
  error: null,
  // The row the Structure group was last filled from.
  picked: null
})
// Counts lookups, so an answer that arrives after the dialog moved on to
// another compound, or after a newer lookup, is dropped.
let lookupRequest = 0

const resetLookup = () => {
  lookupRequest += 1
  Object.assign(lookup, {
    loadingPage: null,
    query: null,
    rows: null,
    total: 0,
    page: 0,
    error: null,
    picked: null
  })
}

const findStructures = async (page = 0) => {
  const request = ++lookupRequest
  const query =
    page === 0 || !lookup.query
      ? {
          formula: input.target_compound_formula,
          cas_number: input.cas_number,
          name: input.target_compound_name
        }
      : lookup.query
  lookup.loadingPage = page
  lookup.error = null
  try {
    const found = await app.data.target.compound.findStructures({
      ...query,
      page,
      limit: STRUCTURES_PAGE
    })
    if (request !== lookupRequest) return
    lookup.query = query
    lookup.rows = [...(page === 0 ? [] : (lookup.rows ?? [])), ...(found?.data ?? [])]
    lookup.total = found?.results ?? 0
    lookup.page = page
    lookup.picked = null
  } catch (error) {
    if (request !== lookupRequest) return
    lookup.error = getApiErrorMessage(error, 'The lookup failed.')
  } finally {
    if (request === lookupRequest) lookup.loadingPage = null
  }
}

// A row's structure replaces the three identity fields together: a SMILES
// left over from another structure would contradict the picked InChIKey. Its
// CAS number replaces the compound's, and a row that records none leaves the
// compound's as it is.
const pick = (row) => {
  Object.assign(input, compoundIdentity(row))
  if (row.cas_number) input.cas_number = row.cas_number
  lookup.picked = row
  lookup.rows = null
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
    resetLookup()
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
          <div class="structure-lookup">
            <Button
              label="Find structure"
              icon="pi pi-search"
              size="small"
              severity="secondary"
              :loading="lookup.loadingPage === 0"
              :disabled="!input.target_compound_formula || lookup.loadingPage !== null"
              v-tooltip="
                'Reference compounds of this formula, the ones of its CAS number and name first'
              "
              @click="findStructures(0)"
            />
            <p v-if="lookup.error" class="structure-lookup-note" role="alert">
              {{ lookup.error }}
            </p>
            <p v-else-if="lookup.picked" class="structure-lookup-note" data-testid="picked">
              Filled from {{ lookup.picked.name || lookup.picked.source_native_id }} ({{
                lookup.picked.source
              }}, {{ lookup.picked.license }}). Save to keep it.
            </p>
            <p
              v-else-if="lookup.rows && !lookup.rows.length"
              class="structure-lookup-note"
              data-testid="no-structures"
            >
              No active reference list has a structure for
              {{ input.target_compound_formula }}.
            </p>
            <ul v-if="lookup.rows?.length" class="structure-lookup-rows" aria-label="Structures">
              <li v-for="row in lookup.rows" :key="`${row.source}:${row.source_native_id}`">
                <Button
                  label="Use"
                  size="small"
                  text
                  :aria-label="`Use ${row.name || row.inchikey || row.source_native_id}`"
                  @click="pick(row)"
                />
                <div class="structure-lookup-row">
                  <span class="structure-lookup-name">
                    {{ row.name || row.source_native_id }}
                    <Tag v-if="row.cas_match" value="CAS" severity="success" />
                    <Tag v-if="row.name_match" value="name" severity="info" />
                  </span>
                  <span class="structure-lookup-key">{{ row.inchikey ?? 'no InChIKey' }}</span>
                  <span class="structure-lookup-source">
                    <template v-if="row.cas_number">CAS {{ row.cas_number }} · </template>
                    {{ row.source }} · {{ row.license }}
                  </span>
                </div>
              </li>
              <li v-if="lookup.rows.length < lookup.total">
                <Button
                  :label="`Show more (${lookup.total - lookup.rows.length} left)`"
                  size="small"
                  text
                  :loading="lookup.loadingPage > 0"
                  :disabled="lookup.loadingPage !== null"
                  @click="findStructures(lookup.page + 1)"
                />
              </li>
            </ul>
          </div>
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

<style scoped>
.structure-lookup {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  align-self: stretch;
  gap: 0.5rem;
  margin-top: 1rem;
  max-width: 22rem;
}
.structure-lookup-note {
  margin: 0;
  font-size: 0.875rem;
}
.structure-lookup-rows {
  list-style: none;
  margin: 0;
  padding: 0;
  width: 100%;
  max-height: 16rem;
  overflow-y: auto;
}
.structure-lookup-rows li {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.25rem 0;
}
.structure-lookup-row {
  display: flex;
  flex-direction: column;
  font-size: 0.875rem;
  min-width: 0;
}
.structure-lookup-name {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-weight: 600;
}
.structure-lookup-key {
  font-family: monospace;
  font-size: 0.8rem;
}
.structure-lookup-source {
  color: var(--p-text-muted-color);
}
</style>
