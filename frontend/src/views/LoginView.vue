<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { auth } from '../auth.js'

const router = useRouter()
const email = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function submit() {
  if (loading.value) return
  error.value = ''
  loading.value = true
  try {
    if (
      await auth.login({ email: email.value.trim(), password: password.value })
    )
      await router.replace({ name: 'welcome' })
  } catch (cause) {
    error.value = cause.message
  } finally {
    password.value = ''
    loading.value = false
  }
}
</script>

<template>
  <section class="auth-layout" aria-labelledby="login-title">
    <div class="intro">
      <span class="eyebrow"
        ><span class="small-dot"></span> TU ESPACIO DE APRENDIZAJE</span
      >
      <h1>Tu próximo paso,<br /><em>en inglés.</em></h1>
      <p>
        Vuelve a tu espacio y sigue construyendo tu confianza, una palabra a la
        vez.
      </p>
      <div class="word-card" aria-hidden="true">
        <span>Little by little.</span><small>Poco a poco.</small>
        <div class="word-line"></div>
      </div>
    </div>
    <div class="form-card">
      <span class="eyebrow">BIENVENIDO DE NUEVO</span>
      <h2 id="login-title">Inicia sesión</h2>
      <p class="card-description">Nos alegra tenerte por aquí.</p>
      <p v-if="auth.state.notice" role="status" class="message success">
        {{ auth.state.notice }}
      </p>
      <p v-if="error" role="alert" class="message error">{{ error }}</p>
      <form :aria-busy="loading" @submit.prevent="submit">
        <label for="email">Email</label>
        <input
          id="email"
          v-model="email"
          type="email"
          name="email"
          autocomplete="username"
          placeholder="tu@email.com"
          required
          :disabled="loading"
        />
        <label for="password">Contraseña</label>
        <input
          id="password"
          v-model="password"
          type="password"
          name="password"
          autocomplete="current-password"
          minlength="12"
          maxlength="128"
          required
          :disabled="loading"
        />
        <button class="primary-button" type="submit" :disabled="loading">
          {{ loading ? 'Iniciando sesión…' : 'Entrar a TrAIner'
          }}<span aria-hidden="true">↗</span>
        </button>
      </form>
      <p class="form-switch">
        ¿Es tu primera vez?
        <RouterLink to="/register">Crea tu cuenta</RouterLink>
      </p>
    </div>
  </section>
</template>
