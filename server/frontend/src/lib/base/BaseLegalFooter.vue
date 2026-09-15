<script setup>
import { copyrightNotice, legalLinks, linkTarget } from '@/lib/about'

// The sign-in screen's legal footer: who makes the software, and where its
// privacy notice, terms and support are published. It sits here and not only
// in the About tab because someone deciding whether to sign in - or a
// reviewer with no account - has to be able to read them first. The links are
// [meta] settings (docs/maintaining.md); one set to empty is not rendered.
const links = legalLinks()
</script>

<template>
  <footer class="legal-footer">
    <span>{{ copyrightNotice }}</span>
    <a
      v-if="links.privacy"
      :href="links.privacy"
      :target="linkTarget(links.privacy)"
      rel="noopener noreferrer"
      >Privacy notice</a
    >
    <a
      v-if="links.terms"
      :href="links.terms"
      :target="linkTarget(links.terms)"
      rel="noopener noreferrer"
      >Terms of service</a
    >
    <a
      v-if="links.support"
      :href="links.support"
      :target="linkTarget(links.support)"
      rel="noopener noreferrer"
      >Support</a
    >
  </footer>
</template>

<style scoped>
.legal-footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 0.25rem 1rem;
  margin-top: 2rem;
  font-size: 0.8rem;
  opacity: 0.7;
}

.legal-footer a {
  color: inherit;
}
</style>
