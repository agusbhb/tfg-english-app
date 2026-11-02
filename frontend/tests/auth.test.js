import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createAuth } from '../src/auth.js'
import { ApiError } from '../src/api.js'
import { sessionGuard } from '../src/sessionGuard.js'

describe('Sesión en memoria', () => {
  let client
  let session
  beforeEach(() => {
    vi.useFakeTimers()
    client = {
      login: vi.fn().mockResolvedValue({
        access_token: 'test-token',
        token_type: 'bearer',
        expires_in: 60,
      }),
      profile: vi.fn().mockResolvedValue({ nombre: 'Ana' }),
    }
    session = createAuth(client)
  })
  afterEach(() => {
    session.logout()
    vi.useRealTimers()
  })

  it('autentica y consulta el perfil con el token recibido', async () => {
    await session.login({ email: 'ana@example.com', password: 'password' })
    await session.loadProfile()
    expect(client.profile).toHaveBeenCalledWith('test-token')
    expect(session.state.user.nombre).toBe('Ana')
    expect(session.state.authenticated).toBe(true)
  })
  it('logout borra la sesión e impide nuevas peticiones privadas', async () => {
    await session.login({})
    session.logout()
    await expect(session.loadProfile()).rejects.toMatchObject({ status: 401 })
    expect(client.profile).not.toHaveBeenCalled()
    expect(session.state.user).toBeNull()
  })
  it('una instancia nueva comienza sin sesión, como al recargar', async () => {
    await session.login({})
    expect(createAuth(client).state.authenticated).toBe(false)
  })
  it('caduca según expires_in', async () => {
    await session.login({})
    vi.advanceTimersByTime(60000)
    expect(session.state.authenticated).toBe(false)
    expect(session.state.notice).toContain('caducado')
  })
  it('un 401 en perfil elimina la sesión', async () => {
    await session.login({})
    client.profile.mockRejectedValue(new ApiError('Unauthorized', 401))
    await expect(session.loadProfile()).rejects.toMatchObject({ status: 401 })
    expect(session.state.authenticated).toBe(false)
  })
  it('un fallo temporal de perfil permite reintentar sin perder el token', async () => {
    await session.login({})
    client.profile.mockRejectedValueOnce(new ApiError('No connection'))
    await expect(session.loadProfile()).rejects.toThrow('No connection')
    expect(session.state.authenticated).toBe(true)
    await session.loadProfile()
    expect(session.state.user.nombre).toBe('Ana')
  })
  it('un login fallido no abre una sesión', async () => {
    client.login.mockRejectedValue(new ApiError('Invalid', 401))
    await expect(session.login({})).rejects.toMatchObject({ status: 401 })
    expect(session.state.authenticated).toBe(false)
  })
  it('rechaza una respuesta de login incompleta', async () => {
    client.login.mockResolvedValue({ access_token: 'token', expires_in: 0 })
    await expect(session.login({})).rejects.toThrow('No se ha podido iniciar')
    expect(session.state.authenticated).toBe(false)
  })
  it('un login pendiente no reabre una sesión cerrada', async () => {
    let resolve
    client.login.mockReturnValue(
      new Promise((done) => {
        resolve = done
      }),
    )
    const pending = session.login({})
    session.logout()
    resolve({ access_token: 'token', token_type: 'bearer', expires_in: 60 })
    expect(await pending).toBe(false)
    expect(session.state.authenticated).toBe(false)
  })
  it('un perfil pendiente no reaparece después de logout', async () => {
    await session.login({})
    let resolve
    client.profile.mockReturnValue(
      new Promise((done) => {
        resolve = done
      }),
    )
    const pending = session.loadProfile()
    session.logout()
    resolve({ nombre: 'Ana' })
    expect(await pending).toBeNull()
    expect(session.state.user).toBeNull()
  })
  it('mantiene la confirmación del registro sin autenticar', () => {
    session.registered()
    expect(session.state.notice).toContain('Tu cuenta está lista')
    expect(session.state.authenticated).toBe(false)
  })
  it('protege rutas privadas y evita volver al login con sesión', async () => {
    const guard = sessionGuard(session)
    expect(guard({ meta: { private: true } })).toEqual({ name: 'login' })
    expect(guard({ meta: { guest: true } })).toBe(true)
    await session.login({})
    expect(guard({ meta: { private: true } })).toBe(true)
    expect(guard({ meta: { guest: true } })).toEqual({ name: 'welcome' })
  })
})
