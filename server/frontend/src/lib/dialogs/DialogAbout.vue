<script setup>
import { computed, ref, watch } from 'vue'

import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'

import { api } from '@/api'
import logoUrl from '@/assets/ultra-trace-logo.png'
import {
  REPOSITORY_URL,
  SECURITY_POLICY_URL,
  builtVersion,
  copyrightNotice,
  legalLinks,
  linkTarget,
  releaseNotesUrl,
  supportLabel,
  versionReport,
  versionsDiffer
} from '@/lib/about'

// What a signed-in user can find out about the product in front of them: the
// exact builds running on both sides - copyable, since that is the first thing
// a support request needs - who makes it and under which licence, and where the
// documentation, legal documents and support live. The licence texts and
// attributions render here rather than linking out: the app is air-gapped by
// design, and a reviewer must be able to read them without leaving it.

const visible = defineModel('visible', { type: Boolean, default: false })

const webVersion = builtVersion()
const serverVersion = ref(null)
const serverFailed = ref(false)
const copied = ref(false)
const links = legalLinks()

const drift = computed(() => versionsDiffer(webVersion, serverVersion.value))
const serverLabel = computed(
  () => serverVersion.value ?? (serverFailed.value ? 'unavailable' : 'checking...')
)

// Re-read on every open: the server can be updated underneath an open tab,
// which is exactly the mismatch this dialog is there to show.
const loadServerVersion = async () => {
  serverVersion.value = null
  serverFailed.value = false
  try {
    const data = await api.http.get('/version', {
      use: 'read',
      type: 'version',
      errors: 'inline'
    })
    serverVersion.value = data?.version ?? null
    serverFailed.value = !serverVersion.value
  } catch {
    serverFailed.value = true
  }
}

watch(
  visible,
  (open) => {
    if (!open) return
    copied.value = false
    loadServerVersion()
  },
  { immediate: true }
)

const copyVersions = async () => {
  try {
    await navigator.clipboard.writeText(
      versionReport({ web: webVersion, server: serverVersion.value })
    )
    copied.value = true
  } catch (error) {
    // No clipboard outside a secure context; the versions stay selectable.
    console.warn(error)
  }
}

// --- Documents, rendered in place ---

const fetchText = async (url) => {
  const response = await fetch(url, { cache: 'no-cache' })
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`)
  return response.text()
}

const DOCUMENTS = {
  license: { title: 'Apache License 2.0', load: () => fetchText('/legal/LICENSE.txt') },
  notice: { title: 'NOTICE', load: () => fetchText('/legal/NOTICE.txt') },
  web: {
    title: 'Third-party notices: web app',
    load: () => fetchText('/legal/THIRD_PARTY_NOTICES.txt')
  },
  server: {
    title: 'Third-party notices: server',
    load: async () =>
      (
        await api.http.get('/version/third-party-notices', {
          responseType: 'text',
          type: 'third_party_notices',
          errors: 'inline'
        })
      ).data,
    // A source checkout has no generated notices; say why rather than just "failed".
    missing:
      'The server has no third-party notices to show. They are generated when its image is built.'
  }
}

const documentOpen = ref(false)
const shown = ref({ title: '', text: '', loading: false, error: null })
let request = 0

const openDocument = async (key) => {
  const { title, load, missing } = DOCUMENTS[key]
  const current = ++request
  shown.value = { title, text: '', loading: true, error: null }
  documentOpen.value = true
  try {
    const text = await load()
    if (current === request) shown.value = { ...shown.value, text, loading: false }
  } catch {
    if (current === request) {
      shown.value = {
        ...shown.value,
        loading: false,
        error: missing ?? 'This document could not be loaded.'
      }
    }
  }
}
</script>

<template>
  <Dialog
    v-model:visible="visible"
    header="About Mascope"
    modal
    :draggable="false"
    :style="{ width: 'min(36rem, 95vw)' }"
  >
    <div class="about">
      <div class="brand">
        <img :src="logoUrl" alt="Ultra Trace" class="wordmark" />
        <b>Mascope</b>
      </div>

      <section aria-labelledby="about-version-heading">
        <h4 id="about-version-heading">Version</h4>
        <dl class="versions">
          <dt>Web app</dt>
          <dd>{{ webVersion ?? 'unknown' }}</dd>
          <dt>Server</dt>
          <dd>{{ serverLabel }}</dd>
        </dl>
        <Message v-if="drift" severity="warn" size="small">
          The web app and the server report different versions. Reload the page; if that does not
          clear it, they are running different builds.
        </Message>
        <div class="actions">
          <Button
            :label="copied ? 'Copied' : 'Copy version details'"
            :icon="copied ? 'pi pi-check' : 'pi pi-clone'"
            size="small"
            severity="secondary"
            @click="copyVersions"
          />
          <Button
            as="a"
            :href="releaseNotesUrl(webVersion)"
            target="_blank"
            rel="noopener noreferrer"
            label="Release notes"
            icon="pi pi-external-link"
            size="small"
            severity="secondary"
            text
          />
        </div>
      </section>

      <section aria-labelledby="about-legal-heading">
        <h4 id="about-legal-heading">Legal</h4>
        <p>
          {{ copyrightNotice }}. Mascope is open-source software licensed under the Apache License
          2.0; its source is published at
          <a :href="REPOSITORY_URL" target="_blank" rel="noopener noreferrer"
            >github.com/ultra-trace-systems/mascope</a
          >.
        </p>
        <div class="actions">
          <Button
            label="Apache License 2.0"
            size="small"
            severity="secondary"
            text
            @click="openDocument('license')"
          />
          <Button
            label="NOTICE"
            size="small"
            severity="secondary"
            text
            @click="openDocument('notice')"
          />
          <Button
            label="Third-party notices (web app)"
            size="small"
            severity="secondary"
            text
            @click="openDocument('web')"
          />
          <Button
            label="Third-party notices (server)"
            size="small"
            severity="secondary"
            text
            @click="openDocument('server')"
          />
        </div>
      </section>

      <section aria-labelledby="about-help-heading">
        <h4 id="about-help-heading">Help and policies</h4>
        <ul class="links">
          <li><a href="/docs/" target="_blank" rel="noopener">User documentation</a></li>
          <li v-if="links.support">
            Support:
            <a
              :href="links.support"
              :target="linkTarget(links.support)"
              rel="noopener noreferrer"
              >{{ supportLabel(links.support) }}</a
            >
          </li>
          <li v-if="links.privacy">
            <a :href="links.privacy" :target="linkTarget(links.privacy)" rel="noopener noreferrer"
              >Privacy notice</a
            >
          </li>
          <li v-if="links.terms">
            <a :href="links.terms" :target="linkTarget(links.terms)" rel="noopener noreferrer"
              >Terms of service</a
            >
          </li>
          <li>
            <a :href="SECURITY_POLICY_URL" target="_blank" rel="noopener noreferrer"
              >Security policy</a
            >
          </li>
        </ul>
      </section>
    </div>
  </Dialog>

  <Dialog
    v-model:visible="documentOpen"
    :header="shown.title"
    modal
    maximizable
    :style="{ width: 'min(60rem, 95vw)' }"
  >
    <ProgressSpinner v-if="shown.loading" />
    <Message v-else-if="shown.error" severity="warn">{{ shown.error }}</Message>
    <pre v-else class="legal-text">{{ shown.text }}</pre>
  </Dialog>
</template>

<style scoped>
.about {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.brand {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  font-size: 15px;
}

.wordmark {
  height: 24px;
  display: block;
}

h4 {
  margin: 0 0 0.5rem;
  opacity: 0.7;
}

p {
  margin: 0;
  line-height: 1.5;
}

.versions {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.25rem 1rem;
  margin: 0 0 0.5rem;
}

.versions dt {
  opacity: 0.7;
}

.versions dd {
  margin: 0;
  font-family: 'IBM Plex Mono', monospace;
  user-select: all;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

.links {
  margin: 0;
  padding-left: 1.25rem;
  line-height: 1.7;
}

.legal-text {
  margin: 0;
  max-height: 70vh;
  overflow: auto;
  white-space: pre-wrap;
  font-size: 0.8rem;
}
</style>
