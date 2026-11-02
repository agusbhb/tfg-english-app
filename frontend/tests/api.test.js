import { describe, expect, it, vi } from 'vitest'
import { ApiError, createApiClient } from '../src/api.js'

describe('Contrato HTTP y errores', () => {
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
