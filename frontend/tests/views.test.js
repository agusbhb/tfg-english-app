// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import App from '../src/App.vue'
import { router } from '../src/router.js'
import { auth } from '../src/auth.js'
import { api, ApiError } from '../src/api.js'

const catalog = [
  {
    id: 'live-subject-a',
    nombre: 'Inglés',
    niveles: [{ nivel_id: 'live-level-a', nombre_curso: '1.º ESO' }],
  },
  {
    id: 'live-subject-b',
    nombre: 'Otra asignatura',
    niveles: [{ nivel_id: 'live-level-b', nombre_curso: '2.º ESO' }],
  },
]
let wrapper
async function open(path) {
  await router.push(path)
  wrapper = mount(App, { global: { plugins: [router] } })
  await flushPromises()
}
async function registration() {
  await wrapper.get('#nombre').setValue('Ana')
  await wrapper.get('#email').setValue('ana@example.com')
  await wrapper.get('#password').setValue(' una clave larga ')
  await wrapper.get('#subject').setValue('live-subject-a')
  await wrapper.get('#level').setValue('live-level-a')
}

beforeEach(() => {
  auth.logout()
  vi.spyOn(api, 'subjects').mockResolvedValue(catalog)
  vi.spyOn(api, 'register').mockResolvedValue({ id: 'user-id' })
  vi.spyOn(api, 'login').mockResolvedValue({
    access_token: 'memory-token',
    token_type: 'bearer',
    expires_in: 60,
  })
  vi.spyOn(api, 'profile').mockResolvedValue({
    nombre: 'Ana',
    email: 'ana@example.com',
  })
})
afterEach(() => {
  wrapper?.unmount()
  auth.logout()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('Flujo de las vistas', () => {
  it('muestra el error de contraseña solo al salir con un valor inválido y lo oculta al corregir', async () => {
    await open('/register')
    expect(wrapper.find('#password-error').exists()).toBe(false)
    await wrapper.get('#password').setValue('corta')
    expect(wrapper.find('#password-error').exists()).toBe(false)
    await wrapper.get('#password').trigger('blur')
    expect(wrapper.get('#password-error').text()).toContain('12 y 128')
    expect(wrapper.get('#password').attributes('aria-invalid')).toBe('true')
    expect(wrapper.get('#password').attributes('aria-describedby')).toBe(
      'password-error',
    )
    await wrapper.get('#password').setValue('una clave válida')
    expect(wrapper.find('#password-error').exists()).toBe(false)
    expect(wrapper.get('#password').attributes('aria-invalid')).toBeUndefined()
    expect(
      wrapper.get('#password').attributes('aria-describedby'),
    ).toBeUndefined()
    await wrapper.get('#password').setValue('corta otra vez')
    await wrapper.get('#password').setValue('corta')
    expect(wrapper.find('#password-error').exists()).toBe(false)
    await wrapper.get('#password').trigger('blur')
    expect(wrapper.find('#password-error').exists()).toBe(true)
  })
  it('muestra el error cuando la validación nativa bloquea el envío', async () => {
    await open('/register')
    await wrapper.get('#password').trigger('invalid')
    expect(wrapper.find('#password-error').exists()).toBe(true)
    expect(api.register).not.toHaveBeenCalled()
  })
  it.each(['', 'corta', 'x'.repeat(129)])(
    'impide enviar una contraseña inválida aunque se dispare submit directamente',
    async (password) => {
      await open('/register')
      await registration()
      await wrapper.get('#password').setValue(password)
      await wrapper.get('form').trigger('submit')
      expect(wrapper.find('#password-error').exists()).toBe(true)
      expect(api.register).not.toHaveBeenCalled()
    },
  )
  it.each([12, 128])(
    'admite el límite de %s caracteres y conserva los espacios',
    async (length) => {
      await open('/register')
      await registration()
      const password = ' ' + 'p'.repeat(length - 2) + ' '
      await wrapper.get('#password').setValue(password)
      await wrapper.get('#password').trigger('blur')
      expect(wrapper.find('#password-error').exists()).toBe(false)
      await wrapper.get('form').trigger('submit')
      await flushPromises()
      expect(api.register.mock.calls[0][0].password).toBe(password)
    },
  )
  it('Register → Login → Bienvenida → Logout con IDs dinámicos y guard de ruta', async () => {
    await open('/register')
    await registration()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.register).toHaveBeenCalledWith({
      nombre: 'Ana',
      email: 'ana@example.com',
      password: ' una clave larga ',
      asignatura_id: 'live-subject-a',
      nivel_id: 'live-level-a',
    })
    expect(router.currentRoute.value.name).toBe('login')
    expect(wrapper.text()).toContain('Tu cuenta está lista')
    await wrapper.get('#email').setValue('ana@example.com')
    await wrapper.get('#password').setValue(' una clave larga ')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.profile).toHaveBeenCalledWith('memory-token')
    expect(wrapper.text()).toContain('Ana!')
    expect(wrapper.text()).toContain('ana@example.com')
    await wrapper.get('.secondary-button').trigger('click')
    await flushPromises()
    expect(auth.state.authenticated).toBe(false)
    expect(wrapper.text()).toContain('Has cerrado sesión correctamente')
    await router.push('/bienvenida')
    expect(router.currentRoute.value.name).toBe('login')
  })
  it('reintenta el catálogo y limpia el nivel al cambiar asignatura', async () => {
    api.subjects.mockRejectedValueOnce(new ApiError('No podemos conectar'))
    await open('/register')
    expect(wrapper.text()).toContain('No podemos conectar')
    expect(wrapper.get('button[type=submit]').element.disabled).toBe(true)
    await wrapper.get('.text-button').trigger('click')
    await flushPromises()
    await registration()
    await wrapper.get('#subject').setValue('live-subject-b')
    expect(wrapper.get('#level').element.value).toBe('')
    expect(wrapper.get('#level').text()).toContain('2.º ESO')
  })
  it('muestra un catálogo vacío e impide enviar el registro', async () => {
    api.subjects.mockResolvedValue([])
    await open('/register')
    expect(wrapper.text()).toContain('no hay asignaturas')
    await wrapper.get('form').trigger('submit')
    expect(api.register).not.toHaveBeenCalled()
  })
  it('muestra un registro duplicado sin abandonar el formulario', async () => {
    api.register.mockRejectedValue(
      new ApiError('Este email ya tiene una cuenta.', 409),
    )
    await open('/register')
    await registration()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain(
      'Este email ya tiene una cuenta',
    )
    expect(wrapper.get('#password').element.value).toBe('')
    expect(router.currentRoute.value.name).toBe('register')
  })
  it('muestra credenciales incorrectas y vuelve a habilitar el login', async () => {
    api.login.mockRejectedValue(
      new ApiError('El email o la contraseña no son correctos.', 401),
    )
    await open('/login')
    await wrapper.get('#email').setValue('ana@example.com')
    await wrapper.get('#password').setValue('wrong-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain('no son correctos')
    expect(wrapper.get('button[type=submit]').element.disabled).toBe(false)
    expect(auth.state.authenticated).toBe(false)
  })
  it('un perfil con token inválido devuelve al login', async () => {
    await auth.login({})
    api.profile.mockRejectedValue(new ApiError('Unauthorized', 401))
    await open('/bienvenida')
    expect(router.currentRoute.value.name).toBe('login')
    expect(wrapper.text()).toContain('Tu sesión ya no es válida')
  })
  it('permite reintentar un fallo temporal al consultar el perfil', async () => {
    await auth.login({})
    api.profile.mockRejectedValueOnce(new ApiError('No podemos conectar'))
    await open('/bienvenida')
    expect(wrapper.get('[role=alert]').text()).toContain('No podemos conectar')
    await wrapper.get('.text-button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Ana!')
  })
  it('la expiración redirige y elimina el contenido privado', async () => {
    vi.useFakeTimers()
    await auth.login({})
    await open('/bienvenida')
    await vi.advanceTimersByTimeAsync(60000)
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('login')
    expect(wrapper.text()).toContain('Tu sesión ha caducado')
    expect(auth.state.user).toBeNull()
  })
})
