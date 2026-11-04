<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api.js'
import { auth } from '../auth.js'

const route = useRoute()
const test = ref(null)
const answers = ref({})
const loading = ref(false)
const sending = ref(false)
const error = ref('')
const saveWarnings = ref({})
let saved = new Map()
let saveQueues = new Map()
let loadVersion = 0
let submissionFinished = Promise.resolve()
const modal = ref(null)
const reviewButton = ref(null)
const answered = computed(
  () =>
    test.value?.preguntas.filter((q) =>
      (answers.value[q.pregunta_id] ?? '').trim(),
    ).length ?? 0,
)
async function load() {
  const version = ++loadVersion
  loading.value = true
  error.value = ''
  test.value = null
  answers.value = {}
  saved = new Map()
  saveQueues = new Map()
  saveWarnings.value = {}
  try {
    const loaded = await auth.authenticated((token) =>
      api.test(route.params.id, token),
    )
    if (version !== loadVersion) return
    test.value = loaded
    saved = new Map(
      (loaded.respuestas_borrador ?? []).map((a) => [
        a.pregunta_id,
        a.respuesta_dada,
      ]),
    )
    answers.value = Object.fromEntries(
      loaded.preguntas.map((q) => [
        q.pregunta_id,
        saved.get(q.pregunta_id) ?? '',
      ]),
    )
  } catch (cause) {
    if (version === loadVersion) error.value = cause.message
  } finally {
    if (version === loadVersion) loading.value = false
  }
}
function saveOnBlur(questionId) {
  if (test.value?.estado !== 'pendiente' || sending.value) return
  const id = test.value.id
  const version = loadVersion
  const value = answers.value[questionId] ?? ''
  // Cola por pregunta: cada trabajo nace de un blur, sin temporizadores.
  // Se compara al ejecutarlo, para respetar el último guardado confirmado.
  const job = (saveQueues.get(questionId) ?? Promise.resolve()).then(
    async () => {
      // Pausar los trabajos nacidos de blur hasta conocer el resultado de entrega.
      await submissionFinished
      if (version !== loadVersion || test.value?.estado !== 'pendiente') return
      if (value === (saved.get(questionId) ?? '')) return
      try {
        await auth.authenticated((token) =>
          api.saveDraft(
            id,
            { pregunta_id: questionId, respuesta_dada: value },
            token,
          ),
        )
        if (version === loadVersion && test.value?.estado === 'pendiente') {
          saved.set(questionId, value)
          delete saveWarnings.value[questionId]
        }
      } catch {
        await submissionFinished
        if (version === loadVersion && test.value?.estado === 'pendiente') {
          saveWarnings.value[questionId] =
            'No se pudo guardar esta respuesta. Puedes seguir y entregarla igualmente; vuelve a salir del campo para reintentar.'
        }
      }
    },
  )
  saveQueues.set(questionId, job)
}
function review() {
  modal.value.showModal()
}
function close() {
  modal.value.close()
  reviewButton.value?.focus()
}
async function submit() {
  if (sending.value) return
  let finishSubmission
  submissionFinished = new Promise((resolve) => {
    finishSubmission = resolve
  })
  sending.value = true
  error.value = ''
  try {
    test.value = await auth.authenticated((token) =>
      api.submitTest(
        route.params.id,
        {
          respuestas: test.value.preguntas.map((q) => ({
            pregunta_id: q.pregunta_id,
            respuesta_dada: answers.value[q.pregunta_id] ?? '',
          })),
        },
        token,
      ),
    )
    await nextTick()
  } catch (cause) {
    error.value = cause.message
    if (cause.status === 409) await load()
  } finally {
    sending.value = false
    finishSubmission()
  }
}
onMounted(load)
watch(() => route.params.id, load)
</script>

<template>
  <section class="practice-layout test-layout">
    <RouterLink to="/practicar">← Elegir otro tema</RouterLink>
    <h1>
      {{
        test?.estado === 'completado' ? 'Tu resultado' : 'Practica tu inglés'
      }}
    </h1>
    <p v-if="loading" role="status">Cargando test…</p>
    <p v-if="error" role="alert" class="message error">{{ error }}</p>
    <button v-if="error && !test" class="text-button" @click="load">
      Volver a intentar
    </button>
    <template v-if="test?.estado === 'pendiente'">
      <p>
        Completa los huecos. Puedes dejar respuestas vacías; contarán como
        incorrectas.
      </p>
      <form @submit.prevent="review">
        <fieldset :disabled="sending">
          <div
            v-for="(question, index) in test.preguntas"
            :key="question.pregunta_id"
            class="exercise-card"
          >
            <label :for="question.pregunta_id"
              >{{ index + 1 }}. {{ question.enunciado }}</label
            >
            <input
              :id="question.pregunta_id"
              v-model="answers[question.pregunta_id]"
              autocomplete="off"
              maxlength="10000"
              @blur="saveOnBlur(question.pregunta_id)"
            />
            <small
              v-if="saveWarnings[question.pregunta_id]"
              class="field-hint"
              role="status"
              >{{ saveWarnings[question.pregunta_id] }}</small
            >
          </div>
          <button ref="reviewButton" type="submit" class="primary-button">
            Revisar y entregar
          </button>
        </fieldset>
      </form>
      <dialog
        ref="modal"
        aria-labelledby="confirm-title"
        @close="reviewButton?.focus()"
      >
        <h2 id="confirm-title">¿Entregar el test?</h2>
        <p>
          Respondidas: {{ answered }}. Pendientes:
          {{ test.total_preguntas - answered }}.
        </p>
        <ol class="review-list">
          <li v-for="question in test.preguntas" :key="question.pregunta_id">
            {{ question.enunciado }}
            <small>{{
              (answers[question.pregunta_id] ?? '').trim()
                ? `Respondida: ${answers[question.pregunta_id]}`
                : 'Pendiente'
            }}</small>
          </li>
        </ol>
        <p>
          Las respuestas vacías contarán como incorrectas. Una vez entregado no
          podrás cambiarlo.
        </p>
        <button class="secondary-button" type="button" :disabled="sending" @click="close">
          Volver y revisar
        </button>
        <button class="primary-button" type="button" :disabled="sending" @click="submit">
          {{ sending ? 'Entregando…' : 'Confirmar entrega' }}
        </button>
        <p v-if="error" role="alert" class="message error">{{ error }}</p>
      </dialog>
    </template>
    <template v-if="test?.estado === 'completado'">
      <p role="status">
        Puntuación: {{ test.puntuacion }} / 10 · Aciertos: {{ test.aciertos }} ·
        Errores: {{ test.total_preguntas - test.aciertos }}
      </p>
      <article
        v-for="question in test.preguntas"
        :key="question.pregunta_id"
        class="exercise-card"
      >
        <h2>{{ question.enunciado }}</h2>
        <template
          v-for="result in test.resultados.filter(
            (r) => r.pregunta_id === question.pregunta_id,
          )"
          :key="result.pregunta_id"
        >
          <p>
            {{ result.es_correcta ? 'Correcta' : 'Incorrecta' }} · Tu respuesta:
            {{ result.respuesta_dada || '(sin respuesta)' }}
          </p>
          <p>Soluciones: {{ result.respuestas_aceptadas.join(' / ') }}</p>
        </template>
      </article>
    </template>
  </section>
</template>
