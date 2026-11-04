import { describe, expect, it, vi } from 'vitest'
import { ApiError, createApiClient } from '../src/api.js'

describe('Contrato HTTP y errores', () => {
  it('guarda una sola pregunta por PATCH con autenticación', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'))
    await createApiClient(fetch).saveDraft(
      'attempt',
      { pregunta_id: 'q1', respuesta_dada: 'x' },
      'token',
    )
    const [url, options] = fetch.mock.calls[0]
    expect(url).toBe('/api/tests/attempt/borrador')
    expect(options.method).toBe('PATCH')
    expect(options.headers.Authorization).toBe('Bearer token')
    expect(JSON.parse(options.body)).toEqual({
      pregunta_id: 'q1',
      respuesta_dada: 'x',
    })
  })
  it('consulta el pendiente y descarta el ID exacto con autenticación', async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response('null'))
    const client = createApiClient(fetch)
    expect(await client.pendingTest('topic', 'token')).toBeNull()
    await client.discardTest('attempt', 'token')
    expect(fetch.mock.calls[0][0]).toBe('/api/tests/pendiente?tema_id=topic')
    expect(fetch.mock.calls[1][0]).toBe('/api/tests/attempt')
    expect(fetch.mock.calls[1][1].method).toBe('DELETE')
    expect(fetch.mock.calls[1][1].headers.Authorization).toBe('Bearer token')
  })
  it('envía las peticiones privadas de práctica con Bearer y JSON', async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response('{}'))
    const client = createApiClient(fetch)
    await client.createTest({ tema_id: 'topic' }, 'token')
    await client.test('attempt', 'token')
    await client.submitTest('attempt', { respuestas: [] }, 'token')
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      '/api/tests',
      '/api/tests/attempt',
      '/api/tests/attempt/respuestas',
    ])
    expect(
      fetch.mock.calls.every(
        (call) => call[1].headers.Authorization === 'Bearer token',
      ),
    ).toBe(true)
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({ respuestas: [] })
  })
  it.each([401, 404, 409, 422])(
    'presenta mensajes de práctica para el estado %s',
    async (status) => {
      const fetch = vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Mensaje de práctica' }), {
          status,
        }),
      )
      await expect(
        createApiClient(fetch).test('attempt', 'token'),
      ).rejects.toMatchObject({
        status,
        message:
          status === 401
            ? 'Tu sesión ya no es válida. Inicia sesión de nuevo.'
            : 'Mensaje de práctica',
      })
    },
  )
  it('envía los campos exactos de registro por JSON sin credenciales del navegador', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ id: 'user' }), { status: 201 }),
      )
    const payload = {
      nombre: 'Ana',
      email: 'ana@example.com',
      password: ' clave larga ',
      asignatura_id: 'subject-id',
      nivel_id: 'level-id',
    }
    await createApiClient(fetch).register(payload)
    const [url, options] = fetch.mock.calls[0]
    expect(url).toBe('/api/auth/register')
    expect(JSON.parse(options.body)).toEqual(payload)
    expect(options.credentials).toBe('omit')
    expect(options.headers).toEqual({ 'Content-Type': 'application/json' })
  })
  it('obtiene el catálogo sin introducir identificadores', async () => {
    const catalog = [
      { id: 'dynamic-subject', niveles: [{ nivel_id: 'dynamic-level' }] },
    ]
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(catalog)))
    expect(await createApiClient(fetch).subjects()).toEqual(catalog)
    expect(fetch.mock.calls[0][0]).toBe('/api/asignaturas')
  })
  it('añade Bearer solo a la petición de perfil', async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response('{}'))
    const client = createApiClient(fetch)
    await client.login({ email: 'ana@example.com', password: 'secret' })
    await client.profile('memory-token')
    expect(fetch.mock.calls[0][1].headers.Authorization).toBeUndefined()
    expect(fetch.mock.calls[1][1].headers.Authorization).toBe(
      'Bearer memory-token',
    )
  })
  it.each([
    [
      401,
      'Credenciales inválidas',
      'El email o la contraseña no son correctos.',
    ],
    [
      409,
      'El email ya está registrado',
      'Este email ya tiene una cuenta. Prueba a iniciar sesión.',
    ],
    [
      422,
      'La selección académica no es válida',
      'La selección académica no es válida',
    ],
    [
      422,
      [{ loc: ['body', 'password'], msg: 'too short' }],
      'Revisa el campo contraseña.',
    ],
    [422, [{ loc: ['body', 'extra'] }], 'Revisa los datos del formulario.'],
    [
      503,
      'database internal',
      'El servicio no está disponible. Inténtalo de nuevo en unos minutos.',
    ],
  ])(
    'traduce el error %s sin exponer detalles internos',
    async (status, detail, message) => {
      const fetch = vi
        .fn()
        .mockResolvedValue(new Response(JSON.stringify({ detail }), { status }))
      await expect(createApiClient(fetch).subjects()).rejects.toMatchObject({
        status,
        message,
      })
    },
  )
  it('explica un fallo de conexión', async () => {
    await expect(
      createApiClient(
        vi.fn().mockRejectedValue(new TypeError('network')),
      ).subjects(),
    ).rejects.toBeInstanceOf(ApiError)
  })
  it('rechaza una respuesta que no es JSON', async () => {
    await expect(
      createApiClient(
        vi.fn().mockResolvedValue(new Response('<html>')),
      ).subjects(),
    ).rejects.toMatchObject({
      message: 'El servicio ha enviado una respuesta inesperada.',
    })
  })
})
