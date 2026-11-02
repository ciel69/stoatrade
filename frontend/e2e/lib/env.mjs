/** Пути и константы прогона. Всё берётся от расположения этого пакета, рабочая папка не важна. */
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

export const E2E_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
export const FRONTEND_DIR = path.resolve(E2E_DIR, '..')
export const DIST_DIR = path.join(FRONTEND_DIR, 'dist')
export const SRC_DIR = path.join(FRONTEND_DIR, 'src')
export const ARTIFACTS_DIR = path.join(E2E_DIR, 'artifacts')

/** Запасные пути к установленному Microsoft Edge (если channel 'msedge' не находится). */
const EDGE_PATHS = [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
]

export function edgePath() {
  return process.env.E2E_EDGE_PATH || EDGE_PATHS.find((p) => existsSync(p)) || null
}

/** Ожидание условий (мс): быстрое для обычных проверок и длинное для сетевого восстановления. */
export const T = { ui: 8000, reconnect: 20000, idleWindow: 20000 }
