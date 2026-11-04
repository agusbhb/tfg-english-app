export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function errorMessage(status, detail, path) {
  if (path.startsWith('/tests')) {
    if (status === 401)
      return 'Tu sesión ya no es válida. Inicia sesión de nuevo.'
    if ([404, 409, 422].includes(status) && typeof detail === 'string')
      return detail
  }
  if (status === 401) return 'El email o la contraseña no son correctos.'
  if (status === 409)
    return 'Este email ya tiene una cuenta. Prueba a iniciar sesión.'
  if (status === 422) {
    if (typeof detail === 'string') return detail
    const field = detail?.[0]?.loc?.at(-1)
    const labels = {
      email: 'email',
      password: 'contraseña',
      nombre: 'nombre',
      asignatura_id: 'asignatura',
      nivel_id: 'nivel académico',
    }
    return labels[field]
      ? `Revisa el campo ${labels[field]}.`
      : 'Revisa los datos del formulario.'
  }
  return 'El servicio no está disponible. Inténtalo de nuevo en unos minutos.'
}

export function createApiClient(
  fetchImpl = globalThis.fetch,
  baseUrl = '/api',
) {
  async function request(path, { method = 'GET', body, token } = {}) {
    let response
    try {
      response = await fetchImpl(`${baseUrl}${path}`, {
        method,
        headers: {
          ...(body ? { 'Content-Type': 'application/json' } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
        credentials: 'omit',
        signal: AbortSignal.timeout(15000),
      })
    } catch {
      throw new ApiError(
        'No podemos conectar con TrAIner. Comprueba tu conexión e inténtalo de nuevo.',
      )
    }
    let data
    try {
      data = await response.json()
    } catch {
      throw new ApiError(
        'El servicio ha enviado una respuesta inesperada.',
        response.status,
      )
    }
    if (!response.ok)
      throw new ApiError(
        errorMessage(response.status, data?.detail, path),
        response.status,
      )
    return data
  }
  return {
    subjects: () => request('/asignaturas'),
    register: (body) => request('/auth/register', { method: 'POST', body }),
    login: (body) => request('/auth/login', { method: 'POST', body }),
    profile: (token) => request('/users/me', { token }),
    createTest: (body, token) =>
      request('/tests', { method: 'POST', body, token }),
    pendingTest: (topic, token) =>
      request(`/tests/pendiente?tema_id=${encodeURIComponent(topic)}`, {
        token,
      }),
    discardTest: (id, token) =>
      request(`/tests/${encodeURIComponent(id)}`, { method: 'DELETE', token }),
    test: (id, token) => request(`/tests/${encodeURIComponent(id)}`, { token }),
    saveDraft: (id, body, token) =>
      request(`/tests/${encodeURIComponent(id)}/borrador`, {
        method: 'PATCH',
        body,
        token,
      }),
    submitTest: (id, body, token) =>
      request(`/tests/${encodeURIComponent(id)}/respuestas`, {
        method: 'POST',
        body,
        token,
      }),
  }
}

export const api = createApiClient()
