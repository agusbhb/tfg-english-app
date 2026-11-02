<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import { auth } from '../auth.js'

const router = useRouter()
const nombre = ref('')
const email = ref('')
const password = ref('')
const passwordChecked = ref(false)
const passwordInvalid = computed(() => {
  const length = [...password.value].length
  return length < 12 || length > 128
})
const showPasswordError = computed(
  () => passwordChecked.value && passwordInvalid.value,
)
watch(password, () => {
  if (!passwordInvalid.value) passwordChecked.value = false
})
const subjectId = ref('')
const levelId = ref('')
const subjects = ref([])
const catalogLoading = ref(true)
const catalogError = ref('')
const loading = ref(false)
const error = ref('')
const levels = computed(
  () =>
    subjects.value.find((item) => item.id === subjectId.value)?.niveles ?? [],
)
watch(subjectId, () => {
  levelId.value = ''
})

async function loadCatalog() {
  catalogLoading.value = true
  catalogError.value = ''
  try {
    subjects.value = await api.subjects()
    if (!subjects.value.length)
      catalogError.value =
        'Todavía no hay asignaturas disponibles. Inténtalo más tarde.'
  } catch (cause) {
    catalogError.value = cause.message
  } finally {
    catalogLoading.value = false
  }
}
onMounted(loadCatalog)

async function submit() {
  if (loading.value || catalogLoading.value || catalogError.value) return
  error.value = ''
  passwordChecked.value = true
  if (passwordInvalid.value) return
  if (
    !nombre.value.trim() ||
    !levels.value.some((level) => level.nivel_id === levelId.value)
  ) {
    error.value =
      'Indica tu nombre y selecciona una asignatura y un nivel válidos.'
    return
  }
  loading.value = true
  try {
    await api.register({
      nombre: nombre.value.trim(),
      email: email.value.trim(),
      password: password.value,
      asignatura_id: subjectId.value,
      nivel_id: levelId.value,
    })
    auth.registered()
    await router.replace({ name: 'login' })
  } catch (cause) {
    error.value = cause.message
  } finally {
    password.value = ''
    passwordChecked.value = false
    loading.value = false
  }
}
</script>

<template>
  <section class="auth-layout" aria-labelledby="register-title">
    <div class="intro">
      <span class="eyebrow"
        ><span class="small-dot"></span> UN NUEVO COMIENZO</span
      >
      <h1>Aprender empieza<br /><em>por un paso.</em></h1>
      <p>
        Crea tu cuenta y cuéntanos en qué curso estás. Este será tu espacio para
        seguir aprendiendo inglés.
      </p>
      <div class="word-card" aria-hidden="true">
        <span>You can do this.</span><small>Tú puedes.</small>
        <div class="word-line"></div>
      </div>
    </div>
    <div class="form-card register-card">
      <span class="eyebrow">EMPECEMOS</span>
      <h2 id="register-title">Crea tu cuenta</h2>
      <p class="card-description">Un pequeño paso para empezar.</p>
      <p v-if="error" role="alert" class="message error">{{ error }}</p>
      <p v-if="catalogLoading" role="status" class="message">
        Cargando asignaturas y cursos…
      </p>
      <div v-if="catalogError" class="message error">
        <p role="alert">{{ catalogError }}</p>
        <button type="button" class="text-button" @click="loadCatalog">
          Volver a intentar
        </button>
      </div>
      <form :aria-busy="loading || catalogLoading" @submit.prevent="submit">
        <fieldset :disabled="loading">
          <label for="nombre">Nombre</label>
          <input
            id="nombre"
            v-model="nombre"
            autocomplete="given-name"
            maxlength="100"
            required
            placeholder="¿Cómo te llamas?"
          />
          <label for="email">Email</label>
          <input
            id="email"
            v-model="email"
            type="email"
            autocomplete="username"
            required
            placeholder="tu@email.com"
          />
          <label for="password">Contraseña</label>
          <input
            id="password"
            v-model="password"
            type="password"
            autocomplete="new-password"
            minlength="12"
            maxlength="128"
            :aria-describedby="showPasswordError ? 'password-error' : undefined"
            :aria-invalid="showPasswordError ? 'true' : undefined"
            @blur="passwordChecked = true"
            @invalid="passwordChecked = true"
            required
          />
          <small
            v-if="showPasswordError"
            id="password-error"
            role="alert"
            class="field-hint field-error"
            >La contraseña debe tener entre 12 y 128 caracteres.</small
          >
          <div class="field-pair">
            <div>
              <label for="subject">Asignatura</label
              ><select
                id="subject"
                v-model="subjectId"
                required
                :disabled="catalogLoading || !!catalogError"
              >
                <option disabled value="">Selecciona</option>
                <option
                  v-for="subject in subjects"
                  :key="subject.id"
                  :value="subject.id"
                >
                  {{ subject.nombre }}
                </option>
              </select>
            </div>
            <div>
              <label for="level">Curso</label
              ><select
                id="level"
                v-model="levelId"
                required
                :disabled="!subjectId || !levels.length"
              >
                <option disabled value="">Selecciona</option>
                <option
                  v-for="level in levels"
                  :key="level.nivel_id"
                  :value="level.nivel_id"
                >
                  {{ level.nombre_curso }}
                </option>
              </select>
            </div>
          </div>
          <button
            class="primary-button"
            type="submit"
            :disabled="loading || catalogLoading || !!catalogError"
          >
            {{ loading ? 'Creando tu cuenta…' : 'Crear cuenta'
            }}<span aria-hidden="true">↗</span>
          </button>
        </fieldset>
      </form>
      <p class="form-switch">
        ¿Ya tienes cuenta? <RouterLink to="/login">Inicia sesión</RouterLink>
      </p>
    </div>
  </section>
</template>
