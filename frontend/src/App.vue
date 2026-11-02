<script setup>
import { watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { auth } from './auth.js'

const route = useRoute()
const router = useRouter()
watch(
  () => auth.state.authenticated,
  (active) => {
    if (!active && route.meta.private) router.replace({ name: 'login' })
  },
)
</script>

<template>
  <div class="app-shell">
    <header class="site-header">
      <RouterLink class="brand" to="/" aria-label="TrAIner, inicio"
        >Tr<span>AI</span>ner<span class="brand-dot">.</span></RouterLink
      >
      <span class="header-note">Un espacio para aprender inglés</span>
    </header>
    <main id="main-content"><RouterView /></main>
    <footer class="site-footer">Paso a paso. A tu ritmo.</footer>
  </div>
</template>
