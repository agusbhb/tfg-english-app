import { reactive, readonly } from 'vue'
import { api, ApiError } from './api.js'

export function createAuth(client = api) {
  const state = reactive({ authenticated: false, user: null, notice: '' })
  let token = null
  let timer = null
  let revision = 0

  function logout(notice = '') {
    revision += 1
    clearTimeout(timer)
    timer = null
    token = null
    state.authenticated = false
    state.user = null
    state.notice = notice
  }

  async function login(credentials) {
    logout()
    const current = revision
    const result = await client.login(credentials)
    if (current !== revision) return false
    if (
      !result.access_token ||
      result.token_type !== 'bearer' ||
      !Number.isFinite(result.expires_in) ||
      result.expires_in <= 0
    ) {
      throw new ApiError(
        'No se ha podido iniciar la sesión. Inténtalo de nuevo.',
      )
    }
    token = result.access_token
    state.authenticated = true
    timer = setTimeout(
      () => logout('Tu sesión ha caducado. Inicia sesión de nuevo.'),
      result.expires_in * 1000,
    )
    return true
  }

  async function loadProfile() {
    if (!token) throw new ApiError('Inicia sesión para continuar.', 401)
    const current = revision
    try {
      const user = await client.profile(token)
      if (current === revision) state.user = user
      return current === revision ? user : null
    } catch (error) {
      if (current === revision && error.status === 401)
        logout('Tu sesión ya no es válida. Inicia sesión de nuevo.')
      throw error
    }
  }

  async function authenticated(operation) {
    if (!token) throw new ApiError('Inicia sesión para continuar.', 401)
    const current = revision
    try {
      const result = await operation(token)
      if (current !== revision)
        throw new ApiError('La sesión ha terminado.', 401)
      return result
    } catch (error) {
      if (current === revision && error.status === 401)
        logout('Tu sesión ya no es válida. Inicia sesión de nuevo.')
      throw error
    }
  }

  return {
    authenticated,
    state: readonly(state),
    login,
    loadProfile,
    logout,
    registered: () => {
      state.notice = 'Tu cuenta está lista. Ya puedes iniciar sesión.'
    },
  }
}

export const auth = createAuth()
