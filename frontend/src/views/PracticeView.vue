<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import { auth } from '../auth.js'

const router = useRouter()
const topics = ref([])
const selected = ref('')
const loading = ref(false)
const starting = ref(false)
const error = ref('')
const pending = ref(null)
const checking = ref(false)
const checked = ref(false)
const discardDialog = ref(null)
const discardButton = ref(null)
let selectionVersion = 0
async function checkPending() {
  const version = ++selectionVersion
  pending.value = null
  checked.value = false
  error.value = ''
  if (!selected.value) {
    checking.value = false
    return
  }
  checking.value = true
  const topic = selected.value
  try {
    const result = await auth.authenticated((token) =>
      api.pendingTest(topic, token),
    )
    if (version === selectionVersion) {
      pending.value = result
      checked.value = true
    }
  } catch (cause) {
    if (version === selectionVersion) error.value = cause.message
  } finally {
    if (version === selectionVersion) checking.value = false
  }
}
watch(selected, checkPending)
async function load() {
  loading.value = true
  error.value = ''
  try {
    await auth.loadProfile()
    const catalog = await api.subjects()
    const profile = auth.state.user?.perfil_academico
    topics.value =
      catalog
        .find((s) => s.id === profile?.asignatura_id)
        ?.niveles.find((n) => n.nivel_id === profile?.nivel_id)?.temas ?? []
  } catch (cause) {
    error.value = cause.message
  } finally {
    loading.value = false
  }
}
async function start() {
  if (
    starting.value ||
    checking.value ||
    !checked.value ||
    pending.value ||
    !selected.value
  )
    return
  starting.value = true
  error.value = ''
  try {
    const test = await auth.authenticated((token) =>
      api.createTest({ tema_id: selected.value }, token),
    )
    await router.push({ name: 'test', params: { id: test.id } })
  } catch (cause) {
    error.value = cause.message
    if (cause.status === 409) await checkPending()
  } finally {
    starting.value = false
  }
}
async function discard() {
  if (starting.value || !pending.value) return
  const id = pending.value.id
  starting.value = true
  error.value = ''
  try {
    await auth.authenticated((token) => api.discardTest(id, token))
    pending.value = null
    discardDialog.value.close()
    starting.value = false
    await start()
  } catch (cause) {
    discardDialog.value.close()
    starting.value = false
    await checkPending()
    error.value = cause.message
  } finally {
    starting.value = false
  }
}
onMounted(load)
</script>

<template>
  <section class="practice-layout">
    <RouterLink to="/bienvenida">← Bienvenida</RouterLink>
    <h1>¿Qué quieres practicar?</h1>
    <p>Elige un tema de tu curso. Prepararemos hasta diez preguntas.</p>
    <p v-if="loading" role="status">Cargando temas…</p>
    <p v-if="error" role="alert" class="message error">{{ error }}</p>
    <button
      v-if="error && !starting"
      class="text-button"
      @click="selected ? checkPending() : load()"
    >
      Volver a intentar
    </button>
    <p v-if="!loading && !error && !topics.length">
      Todavía no hay temas disponibles para tu curso.
    </p>
    <form v-if="topics.length" @submit.prevent="start">
      <label for="topic">Tema</label>
      <select id="topic" v-model="selected" required :disabled="starting">
        <option disabled value="">Selecciona un tema</option>
        <option
          v-for="topic in topics"
          :key="topic.tema_id"
          :value="topic.tema_id"
        >
          {{ topic.nombre }}
        </option>
      </select>
      <p v-if="checking" role="status">Buscando un test pendiente…</p>
      <template v-if="pending">
        <p role="status">
          Tienes un test pendiente de este tema. Puedes continuar con tus
          respuestas guardadas.
        </p>
        <button
          type="button"
          class="primary-button"
          :disabled="starting"
          @click="router.push({ name: 'test', params: { id: pending.id } })"
        >
          Continuar test
        </button>
        <button
          ref="discardButton"
          type="button"
          class="secondary-button"
          :disabled="starting"
          @click="discardDialog.showModal()"
        >
          Descartar y empezar otro
        </button>
      </template>
      <button
        v-else
        class="primary-button"
        :disabled="starting || checking || !checked || !selected"
      >
        {{ starting ? 'Preparando…' : 'Empezar test' }}
      </button>
    </form>
    <dialog
      ref="discardDialog"
      aria-labelledby="discard-title"
      @cancel="starting && $event.preventDefault()"
      @close="discardButton?.focus()"
    >
      <h2 id="discard-title">¿Descartar este test pendiente?</h2>
      <p>
        Se eliminará este intento y se intentará crear uno nuevo. Los tests
        completados se conservan.
      </p>
      <button
        type="button"
        class="secondary-button"
        :disabled="starting"
        @click="discardDialog.close()"
      >
        Volver
      </button>
      <button
        type="button"
        class="primary-button"
        :disabled="starting"
        @click="discard"
      >
        {{ starting ? 'Descartando…' : 'Confirmar descarte' }}
      </button>
    </dialog>
  </section>
</template>
