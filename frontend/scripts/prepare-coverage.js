import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { dirname, resolve, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

// Solo modifica nombres de archivo: conserva contadores y líneas del LCOV.
const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const report = resolve(frontend, 'coverage/lcov.info')
let count = 0
const lines = readFileSync(report, 'utf8')
  .split('\n')
  .map((line) => {
    if (!line.startsWith('SF:')) return line
    const filename = line.slice(3).replace(/^frontend\//, '')
    const path = resolve(frontend, filename)
    const local = relative(frontend, path).split(sep).join('/')
    if (!local.startsWith('src/') || !existsSync(path))
      throw new Error(`Ruta de cobertura inválida: ${filename}`)
    count += 1
    return `SF:frontend/${local}`
  })
if (!count) throw new Error('El informe LCOV no contiene archivos')
writeFileSync(report, lines.join('\n'))
console.log(
  `LCOV válido: ${count} archivos del frontend resueltos desde la raíz del repositorio`,
)
