/**
 * Запуск всех сценариев: `npm --prefix frontend/e2e test` (или `node --experimental-strip-types run.mjs [фильтр]`).
 * Фильтр: часть имени файла сценария (например `risk` запустит specs/03-risk.mjs). Нужен собранный frontend/dist.
 */
import { readdirSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

const dir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'specs')
const filter = process.argv[2]
const files = readdirSync(dir).filter((f) => f.endsWith('.mjs') && (!filter || f.includes(filter))).sort()
if (!files.length) {
  console.error(`Нет сценариев по фильтру «${filter}»`)
  process.exit(1)
}
for (const f of files) await import(pathToFileURL(path.join(dir, f)).href)
