<script setup>
/**
 * The dataset dialog's descriptive-metadata section: licence, authors,
 * keywords, related identifiers and funding.
 *
 * `v-model` is the form `metadataForm` builds; every change emits a new form
 * object, and the dialog turns it into request fields with `metadataPayload`.
 */
import { ref } from 'vue'

import Button from 'primevue/button'
import Chip from 'primevue/chip'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'

import {
  IDENTIFIER_TYPE_SUGGESTIONS,
  LICENSE_SUGGESTIONS,
  RELATION_SUGGESTIONS,
  addKeyword,
  emptyAuthor,
  emptyFunding,
  emptyRelatedIdentifier,
  moveUp,
  orcidInvalid
} from './datasetMetadata.js'

const model = defineModel({ type: Object, required: true })

const keyword = ref('')

function set(field, value) {
  model.value = { ...model.value, [field]: value }
}

function setRow(field, index, key, value) {
  set(
    field,
    model.value[field].map((row, i) => (i === index ? { ...row, [key]: value } : row))
  )
}

function addRow(field, empty) {
  set(field, [...model.value[field], empty()])
}

function removeRow(field, index) {
  set(
    field,
    model.value[field].filter((_, i) => i !== index)
  )
}

function commitKeyword() {
  set('keywords', addKeyword(model.value.keywords, keyword.value))
  keyword.value = ''
}
</script>

<template>
  <fieldset class="dataset-metadata">
    <legend>Descriptive metadata</legend>

    <label for="dataset-license">License</label>
    <Select
      inputId="dataset-license"
      editable
      showClear
      :options="LICENSE_SUGGESTIONS"
      :modelValue="model.license"
      @update:modelValue="set('license', $event ?? '')"
      placeholder="e.g. CC-BY-4.0"
    />

    <h4>Authors</h4>
    <div v-for="(author, index) in model.authors" :key="index" class="row" data-row="author">
      <InputText
        aria-label="Author name"
        placeholder="Name"
        :modelValue="author.name"
        @update:modelValue="setRow('authors', index, 'name', $event)"
      />
      <InputText
        aria-label="Author ORCID"
        placeholder="ORCID 0000-0000-0000-0000"
        :invalid="orcidInvalid(author.orcid)"
        :modelValue="author.orcid"
        @update:modelValue="setRow('authors', index, 'orcid', $event)"
      />
      <InputText
        aria-label="Author affiliation"
        placeholder="Affiliation"
        :modelValue="author.affiliation"
        @update:modelValue="setRow('authors', index, 'affiliation', $event)"
      />
      <Button
        icon="pi pi-arrow-up"
        aria-label="Move author up"
        text
        :disabled="index === 0"
        @click="set('authors', moveUp(model.authors, index))"
      />
      <Button
        icon="pi pi-trash"
        aria-label="Remove author"
        text
        @click="removeRow('authors', index)"
      />
    </div>
    <Button label="Add author" icon="pi pi-plus" text @click="addRow('authors', emptyAuthor)" />

    <h4>Keywords</h4>
    <div class="chips">
      <Chip
        v-for="(word, index) in model.keywords"
        :key="word"
        :label="word"
        removable
        @remove="removeRow('keywords', index)"
      />
    </div>
    <InputText
      aria-label="Keyword"
      placeholder="Type a keyword, press Enter"
      v-model="keyword"
      @keydown.enter.prevent="commitKeyword"
      @blur="commitKeyword"
    />

    <h4>Related identifiers</h4>
    <div
      v-for="(entry, index) in model.related_identifiers"
      :key="index"
      class="row"
      data-row="related"
    >
      <Select
        aria-label="Relation"
        editable
        placeholder="Relation"
        :options="RELATION_SUGGESTIONS"
        :modelValue="entry.relation"
        @update:modelValue="setRow('related_identifiers', index, 'relation', $event ?? '')"
      />
      <InputText
        aria-label="Identifier"
        placeholder="e.g. 10.1234/abcd"
        :modelValue="entry.identifier"
        @update:modelValue="setRow('related_identifiers', index, 'identifier', $event)"
      />
      <Select
        aria-label="Identifier type"
        editable
        placeholder="Type"
        :options="IDENTIFIER_TYPE_SUGGESTIONS"
        :modelValue="entry.identifier_type"
        @update:modelValue="setRow('related_identifiers', index, 'identifier_type', $event ?? '')"
      />
      <Button
        icon="pi pi-trash"
        aria-label="Remove related identifier"
        text
        @click="removeRow('related_identifiers', index)"
      />
    </div>
    <Button
      label="Add related identifier"
      icon="pi pi-plus"
      text
      @click="addRow('related_identifiers', emptyRelatedIdentifier)"
    />

    <h4>Funding</h4>
    <div v-for="(entry, index) in model.funding" :key="index" class="row" data-row="funding">
      <InputText
        aria-label="Funder"
        placeholder="Funder"
        :modelValue="entry.funder"
        @update:modelValue="setRow('funding', index, 'funder', $event)"
      />
      <InputText
        aria-label="Award"
        placeholder="Award number"
        :modelValue="entry.award"
        @update:modelValue="setRow('funding', index, 'award', $event)"
      />
      <Button
        icon="pi pi-trash"
        aria-label="Remove funding"
        text
        @click="removeRow('funding', index)"
      />
    </div>
    <Button label="Add funding" icon="pi pi-plus" text @click="addRow('funding', emptyFunding)" />
  </fieldset>
</template>

<style scoped>
.dataset-metadata {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  border: none;
  padding: 0;
  margin: 0;
}

.dataset-metadata legend {
  font-weight: 600;
  margin-bottom: 0.5rem;
}

.dataset-metadata h4 {
  margin: 0.75rem 0 0;
}

.row {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.row > :deep(input),
.row > :deep(.p-select) {
  flex: 1;
  min-width: 0;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}
</style>
