<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { auth } from '../auth.js'

const router = useRouter()
const loading = ref(true)
const error = ref('')
async function loadProfile() {
  loading.value = true
  error.value = ''
  try {
    await auth.loadProfile()
  } catch (cause) {
    if (cause.status !== 401) error.value = cause.message
  } finally {
    loading.value = false
  }
}
onMounted(loadProfile)
function logout() {
  auth.logout('Has cerrado sesión correctamente.')
  router.replace({ name: 'login' })
}
</script>

<template>
  <section class="welcome-layout" aria-labelledby="welcome-title">
    <div class="welcome-mark" aria-hidden="true">↗</div>
    <span class="eyebrow">ESTÁS EN TU ESPACIO</span>
    <p v-if="loading" role="status">Preparando tu bienvenida…</p>
    <template v-else-if="auth.state.user && !error">
      <h1 id="welcome-title">
        ¡Bienvenido a TrAIner,<br /><em
          >{{ auth.state.user.nombre || 'estudiante' }}!</em
        >
      </h1>
      <p>Nos alegra verte. Este es el comienzo de tu camino con el inglés.</p>
      <span class="profile-email">{{ auth.state.user.email }}</span>
      <p><RouterLink to="/practicar">Practicar inglés</RouterLink></p>
    </template>
    <div v-if="error" class="message error">
      <p role="alert">{{ error }}</p>
      <button class="text-button" @click="loadProfile">
        Volver a intentar
      </button>
    </div>
    <button class="secondary-button" @click="logout">Cerrar sesión</button>
  </section>
</template>
