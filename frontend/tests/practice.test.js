// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import App from '../src/App.vue'
import { router } from '../src/router.js'
import { auth } from '../src/auth.js'
import { api, ApiError } from '../src/api.js'

let wrapper
const pending = {
  id: 'attempt',
  tema_id: 'topic',
  estado: 'pendiente',
  total_preguntas: 2,
  preguntas: [
    { pregunta_id: 'q1', formato: 'fill_blanks', enunciado: 'She ___ TV.' },
    {
      pregunta_id: 'q2',
      formato: 'fill_blanks',
      enunciado: 'They ___ English.',
    },
  ],
}
const completed = {
  ...pending,
  estado: 'completado',
  aciertos: 1,
  puntuacion: 5,
  resultados: [
    {
      pregunta_id: 'q1',
      respuesta_dada: 'watches',
      es_correcta: true,
      respuestas_aceptadas: ['watches'],
    },
    {
      pregunta_id: 'q2',
      respuesta_dada: '',
      es_correcta: false,
      respuestas_aceptadas: ['study'],
    },
  ],
}
beforeEach(async () => {
  auth.logout()
  vi.spyOn(api, 'login').mockResolvedValue({
    access_token: 'token',
    token_type: 'bearer',
    expires_in: 100,
  })
  vi.spyOn(api, 'profile').mockResolvedValue({
    nombre: 'Ana',
    perfil_academico: { asignatura_id: 'subject', nivel_id: 'level' },
  })
  vi.spyOn(api, 'subjects').mockResolvedValue([
    {
      id: 'subject',
      niveles: [
        {
          nivel_id: 'other',
          temas: [{ tema_id: 'wrong', nombre: 'No visible' }],
        },
        {
          nivel_id: 'level',
          temas: [{ tema_id: 'topic', nombre: 'Present Simple' }],
        },
      ],
    },
  ])
  vi.spyOn(api, 'createTest').mockResolvedValue(pending)
  vi.spyOn(api, 'pendingTest').mockResolvedValue(null)
  vi.spyOn(api, 'discardTest').mockResolvedValue({ descartado: true })
  vi.spyOn(api, 'test').mockResolvedValue(pending)
  vi.spyOn(api, 'submitTest').mockResolvedValue(completed)
  vi.spyOn(api, 'saveDraft').mockImplementation(async (_id, body) => body)
  await auth.login({})
})
afterEach(() => {
  wrapper?.unmount()
  auth.logout()
  vi.restoreAllMocks()
})
async function open(path) {
  await router.push(path)
  wrapper = mount(App, { global: { plugins: [router] } })
  await flushPromises()
}
it('selecciona solo temas propios y confirma antes de enviar respuestas vacías', async () => {
  await open('/practicar')
  expect(wrapper.text()).not.toContain('No visible')
  await wrapper.get('#topic').setValue('topic')
  await flushPromises()
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(api.createTest).toHaveBeenCalledWith({ tema_id: 'topic' }, 'token')
  await wrapper.get('#q1').setValue('watches')
  await wrapper.get('form').trigger('submit')
  expect(api.submitTest).not.toHaveBeenCalled()
  expect(wrapper.get('dialog').text()).toContain(
    'Respondidas: 1. Pendientes: 1.',
  )
  expect(wrapper.get('.review-list').text()).toContain('Respondida: watches')
  expect(wrapper.get('.review-list').text()).toContain('Pendiente')
  await wrapper.get('dialog .secondary-button').trigger('click')
  expect(wrapper.get('#q1').element.value).toBe('watches')
  await wrapper.get('form').trigger('submit')
  await wrapper.get('dialog .primary-button').trigger('click')
  await flushPromises()
  expect(api.submitTest).toHaveBeenCalledWith(
    'attempt',
    {
      respuestas: [
        { pregunta_id: 'q1', respuesta_dada: 'watches' },
        { pregunta_id: 'q2', respuesta_dada: '' },
      ],
    },
    'token',
  )
  expect(wrapper.text()).toContain('Puntuación: 5 / 10')
  expect(wrapper.text()).toContain('Soluciones: study')
})
it('recupera el resultado persistido sin volver a entregar', async () => {
  api.test.mockResolvedValue(completed)
  await open('/tests/attempt')
  expect(wrapper.text()).toContain('Errores: 1')
  expect(wrapper.find('form').exists()).toBe(false)
  expect(api.submitTest).not.toHaveBeenCalled()
})

it('recupera el borrador sin guardar al cargar, escribir o repetir un blur sin cambios', async () => {
  api.test.mockResolvedValue({
    ...pending,
    respuestas_borrador: [{ pregunta_id: 'q1', respuesta_dada: ' watches ' }],
  })
  await open('/tests/attempt')
  expect(wrapper.get('#q1').element.value).toBe(' watches ')
  expect(api.saveDraft).not.toHaveBeenCalled()
  await wrapper.get('#q1').trigger('blur')
  await flushPromises()
  expect(api.saveDraft).not.toHaveBeenCalled()
  await wrapper.get('#q1').setValue('different')
  await flushPromises()
  expect(api.saveDraft).not.toHaveBeenCalled()
  await wrapper.get('#q1').trigger('blur')
  await flushPromises()
  expect(api.saveDraft).toHaveBeenCalledWith(
    'attempt',
    { pregunta_id: 'q1', respuesta_dada: 'different' },
    'token',
  )
  await wrapper.get('#q1').trigger('blur')
  await flushPromises()
  expect(api.saveDraft).toHaveBeenCalledTimes(1)
})

it('guarda una respuesta vacía al borrar un valor que estaba guardado', async () => {
  api.test.mockResolvedValue({
    ...pending,
    respuestas_borrador: [{ pregunta_id: 'q1', respuesta_dada: 'watches' }],
  })
  await open('/tests/attempt')
  await wrapper.get('#q1').setValue('')
  await wrapper.get('#q1').trigger('blur')
  await flushPromises()
  expect(api.saveDraft).toHaveBeenCalledWith(
    'attempt',
    { pregunta_id: 'q1', respuesta_dada: '' },
    'token',
  )
})

it('avisa del fallo sin bloquear y reintenta solo con otro blur', async () => {
  api.saveDraft.mockRejectedValueOnce(new ApiError('Sin conexión'))
  await open('/tests/attempt')
  await wrapper.get('#q1').setValue('watches')
  await wrapper.get('#q1').trigger('blur')
  await flushPromises()
  expect(wrapper.text()).toContain('No se pudo guardar esta respuesta')
  expect(wrapper.get('#q1').element.disabled).toBe(false)
  expect(wrapper.get('#q1').element.value).toBe('watches')
  await wrapper.get('#q1').trigger('blur')
  await flushPromises()
  expect(api.saveDraft).toHaveBeenCalledTimes(2)
  expect(wrapper.text()).not.toContain('No se pudo guardar esta respuesta')
})

it('ordena los blurs de una pregunta sin sobrescribir lo que se sigue escribiendo', async () => {
  let resolveFirst
  api.saveDraft.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveFirst = resolve
      }),
  )
  await open('/tests/attempt')
  await wrapper.get('#q1').setValue('first')
  await wrapper.get('#q1').trigger('blur')
  await flushPromises()
  await wrapper.get('#q1').setValue('second')
  await wrapper.get('#q1').trigger('blur')
  await flushPromises()
  expect(api.saveDraft).toHaveBeenCalledTimes(1)
  resolveFirst({})
  await flushPromises()
  expect(api.saveDraft).toHaveBeenCalledTimes(2)
  expect(
    api.saveDraft.mock.calls.map((call) => call[1].respuesta_dada),
  ).toEqual(['first', 'second'])
  expect(wrapper.get('#q1').element.value).toBe('second')
})

it('entrega el formulario actual sin esperar un guardado pendiente', async () => {
  let resolveSave
  api.saveDraft.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveSave = resolve
      }),
  )
  await open('/tests/attempt')
  await wrapper.get('#q1').setValue('old draft')
  await wrapper.get('#q1').trigger('blur')
  await flushPromises()
  await wrapper.get('#q1').setValue('watches')
  await wrapper.get('form').trigger('submit')
  await wrapper.get('dialog .primary-button').trigger('click')
  await flushPromises()
  expect(api.submitTest.mock.calls[0][1].respuestas[0].respuesta_dada).toBe(
    'watches',
  )
  expect(wrapper.text()).toContain('Tu resultado')
  resolveSave({})
  await flushPromises()
  expect(wrapper.find('#q1').exists()).toBe(false)
  expect(wrapper.text()).not.toContain('No se pudo guardar')
})

it('no guarda por volver a temas si no hubo blur', async () => {
  await open('/tests/attempt')
  await wrapper.get('#q1').setValue('not blurred')
  await router.push('/practicar')
  await flushPromises()
  expect(api.saveDraft).not.toHaveBeenCalled()
})

it('reanuda en orden los blurs en cola tras fallar la entrega y conserva el último valor', async () => {
  let resolveSave, rejectSubmit
  api.saveDraft.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveSave = resolve
      }),
  )
  api.submitTest.mockImplementationOnce(
    () =>
      new Promise((_resolve, reject) => {
        rejectSubmit = reject
      }),
  )
  await open('/tests/attempt')
  await wrapper.get('#q1').setValue('first')
  await wrapper.get('#q1').trigger('blur')
  await flushPromises()
  for (const value of ['second', 'latest']) {
    await wrapper.get('#q1').setValue(value)
    await wrapper.get('#q1').trigger('blur')
  }
  await wrapper.get('form').trigger('submit')
  await wrapper.get('dialog .primary-button').trigger('click')
  await flushPromises()
  expect(api.submitTest.mock.calls[0][1].respuestas[0].respuesta_dada).toBe(
    'latest',
  )
  resolveSave({})
  await flushPromises()
  expect(api.saveDraft).toHaveBeenCalledTimes(1)
  rejectSubmit(new ApiError('Entrega fallida'))
  await flushPromises()
  expect(
    api.saveDraft.mock.calls.map((call) => call[1].respuesta_dada),
  ).toEqual(['first', 'second', 'latest'])
  expect(wrapper.get('#q1').element.value).toBe('latest')
  await wrapper.get('#q1').trigger('blur')
  await flushPromises()
  expect(api.saveDraft).toHaveBeenCalledTimes(3)
})

it('descarta los blurs en cola si la entrega tiene éxito, sin nuevos PATCH', async () => {
  let resolveSave, resolveSubmit
  api.saveDraft.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveSave = resolve
      }),
  )
  api.submitTest.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveSubmit = resolve
      }),
  )
  await open('/tests/attempt')
  await wrapper.get('#q1').setValue('first')
  await wrapper.get('#q1').trigger('blur')
  await flushPromises()
  await wrapper.get('#q1').setValue('latest')
  await wrapper.get('#q1').trigger('blur')
  await wrapper.get('form').trigger('submit')
  await wrapper.get('dialog .primary-button').trigger('click')
  await flushPromises()
  resolveSave({})
  await flushPromises()
  expect(api.saveDraft).toHaveBeenCalledTimes(1)
  resolveSubmit(completed)
  await flushPromises()
  expect(api.saveDraft).toHaveBeenCalledTimes(1)
  expect(wrapper.text()).toContain('Tu resultado')
})

it.each([false, true])(
  'avisa si falla también el autoguardado, en vuelo=%s',
  async (inFlight) => {
    let finishSave, rejectSubmit
    api.saveDraft.mockImplementationOnce(
      () =>
        new Promise((resolve, reject) => {
          finishSave = inFlight ? reject : resolve
        }),
    )
    if (!inFlight)
      api.saveDraft.mockRejectedValueOnce(new ApiError('Guardado fallido'))
    api.submitTest.mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectSubmit = reject
        }),
    )
    await open('/tests/attempt')
    await wrapper.get('#q1').setValue('first')
    await wrapper.get('#q1').trigger('blur')
    await flushPromises()
    if (!inFlight) {
      await wrapper.get('#q1').setValue('latest')
      await wrapper.get('#q1').trigger('blur')
    }
    await wrapper.get('form').trigger('submit')
    await wrapper.get('dialog .primary-button').trigger('click')
    await flushPromises()
    finishSave(new ApiError('Guardado fallido'))
    await flushPromises()
    rejectSubmit(new ApiError('Entrega fallida'))
    await flushPromises()
    expect(wrapper.text()).toContain('No se pudo guardar esta respuesta')
    expect(wrapper.get('#q1').element.disabled).toBe(false)
    expect(wrapper.get('#q1').element.value).toBe(inFlight ? 'first' : 'latest')
  },
)
it('explica ausencia de ejercicios', async () => {
  api.createTest.mockRejectedValue(
    new ApiError('No hay ejercicios disponibles para este tema', 404),
  )
  await open('/practicar')
  await wrapper.get('#topic').setValue('topic')
  await flushPromises()
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper.get('[role=alert]').text()).toContain('No hay ejercicios')
})

it('ofrece continuar con el mismo ID sin crear ni eliminar documentos', async () => {
  api.pendingTest.mockResolvedValue(pending)
  await open('/practicar')
  await wrapper.get('#topic').setValue('topic')
  await flushPromises()
  expect(api.pendingTest).toHaveBeenCalledWith('topic', 'token')
  expect(wrapper.text()).toContain('Continuar test')
  const button = wrapper
    .findAll('button')
    .find((b) => b.text() === 'Continuar test')
  await button.trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.params.id).toBe('attempt')
  expect(api.test).toHaveBeenCalledWith('attempt', 'token')
  expect(wrapper.get('#q1').element.value).toBe('')
  expect(api.createTest).not.toHaveBeenCalled()
  expect(api.discardTest).not.toHaveBeenCalled()
})

it('descartar necesita confirmación y solo después crea otro test', async () => {
  api.pendingTest.mockResolvedValue(pending)
  api.createTest.mockResolvedValue({ ...pending, id: 'replacement' })
  await open('/practicar')
  await wrapper.get('#topic').setValue('topic')
  await flushPromises()
  await wrapper.get('form .secondary-button').trigger('click')
  expect(api.discardTest).not.toHaveBeenCalled()
  await wrapper.get('dialog .secondary-button').trigger('click')
  expect(api.discardTest).not.toHaveBeenCalled()
  await wrapper.get('form .secondary-button').trigger('click')
  api.discardTest.mockImplementationOnce(async () => {
    expect(api.createTest).not.toHaveBeenCalled()
    return { descartado: true }
  })
  await wrapper.get('dialog .primary-button').trigger('click')
  await flushPromises()
  expect(api.discardTest).toHaveBeenCalledWith('attempt', 'token')
  expect(api.createTest).toHaveBeenCalledTimes(1)
  expect(router.currentRoute.value.params.id).toBe('replacement')
})

it('si falla descartar no crea otro y explica el error', async () => {
  api.pendingTest.mockResolvedValue(pending)
  api.discardTest.mockRejectedValue(
    new ApiError('El test ya no está pendiente', 409),
  )
  await open('/practicar')
  await wrapper.get('#topic').setValue('topic')
  await flushPromises()
  await wrapper.get('form .secondary-button').trigger('click')
  await wrapper.get('dialog .primary-button').trigger('click')
  await flushPromises()
  expect(api.createTest).not.toHaveBeenCalled()
  expect(wrapper.get('[role=alert]').text()).toContain('ya no está pendiente')
})

it('un conflicto al crear recupera el pendiente creado en otra pestaña', async () => {
  api.createTest.mockRejectedValue(new ApiError('Ya existe un pendiente', 409))
  api.pendingTest.mockResolvedValueOnce(null).mockResolvedValue(pending)
  await open('/practicar')
  await wrapper.get('#topic').setValue('topic')
  await flushPromises()
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper.text()).toContain('Continuar test')
  expect(router.currentRoute.value.name).toBe('practice')
})

it('un fallo al consultar pendientes bloquea la creación hasta reintentar', async () => {
  api.pendingTest.mockRejectedValueOnce(new ApiError('Sin conexión'))
  await open('/practicar')
  await wrapper.get('#topic').setValue('topic')
  await flushPromises()
  await wrapper.get('form').trigger('submit')
  expect(api.createTest).not.toHaveBeenCalled()
  await wrapper.get('.text-button').trigger('click')
  await flushPromises()
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(api.createTest).toHaveBeenCalledTimes(1)
})

it('ignora la consulta anterior cuando el alumno cambia de tema', async () => {
  api.subjects.mockResolvedValue([
    {
      id: 'subject',
      niveles: [
        {
          nivel_id: 'level',
          temas: [
            { tema_id: 'topic', nombre: 'Primero' },
            { tema_id: 'second', nombre: 'Segundo' },
          ],
        },
      ],
    },
  ])
  let resolveFirst
  api.pendingTest
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve
        }),
    )
    .mockResolvedValue(null)
  await open('/practicar')
  await wrapper.get('#topic').setValue('topic')
  await wrapper.get('#topic').setValue('second')
  await flushPromises()
  resolveFirst(pending)
  await flushPromises()
  expect(wrapper.text()).not.toContain('Continuar test')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(api.createTest).toHaveBeenCalledWith({ tema_id: 'second' }, 'token')
})
it('limpia la sesión al recibir 401 en una petición de test', async () => {
  api.test.mockRejectedValue(new ApiError('Unauthorized', 401))
  await open('/tests/attempt')
  expect(auth.state.authenticated).toBe(false)
  expect(router.currentRoute.value.name).toBe('login')
})
it('permite reintentar un error temporal conservando respuestas', async () => {
  api.submitTest.mockRejectedValueOnce(new ApiError('Sin conexión'))
  await open('/tests/attempt')
  await wrapper.get('#q1').setValue('watches')
  await wrapper.get('form').trigger('submit')
  await wrapper.get('dialog .primary-button').trigger('click')
  await flushPromises()
  expect(wrapper.get('#q1').element.value).toBe('watches')
  expect(wrapper.get('dialog [role=alert]').text()).toContain('Sin conexión')
  await wrapper.get('dialog .primary-button').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('Tu resultado')
})
