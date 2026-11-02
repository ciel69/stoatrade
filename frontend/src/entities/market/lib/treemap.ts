export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export interface TreeItem {
  key: string
  weight: number
}

/** Самое «вытянутое» отношение сторон в ряду плиток шириной side (чем ближе к 1, тем ближе плитки к квадрату). */
function worst(areas: number[], side: number): number {
  const s = areas.reduce((a, b) => a + b, 0)
  return Math.max((side * side * Math.max(...areas)) / (s * s), (s * s) / (side * side * Math.min(...areas)))
}

/**
 * Раскладка squarified treemap: площадь плитки пропорциональна весу, плитки как можно ближе к квадрату, всё в пределах рамки без зазоров.
 * Детерминирована: одинаковые веса дают одинаковую раскладку (порядок по убыванию веса, при равенстве как во входе). Нулевые и отрицательные веса отбрасываются.
 */
export function squarify(items: TreeItem[], box: Rect): Map<string, Rect> {
  const out = new Map<string, Rect>()
  const rows = items.filter((i) => i.weight > 0 && Number.isFinite(i.weight)).map((i, n) => ({ ...i, n })).sort((a, b) => b.weight - a.weight || a.n - b.n)
  const total = rows.reduce((a, b) => a + b.weight, 0)
  if (!rows.length || box.w <= 0 || box.h <= 0) return out
  const k = (box.w * box.h) / total
  let { x, y, w, h } = box
  let i = 0
  while (i < rows.length) {
    const side = Math.min(w, h)
    const row = [rows[i]]
    let areas = [rows[i].weight * k]
    while (i + row.length < rows.length) {
      const next = rows[i + row.length].weight * k
      if (worst([...areas, next], side) > worst(areas, side)) break
      row.push(rows[i + row.length])
      areas = [...areas, next]
    }
    const sum = areas.reduce((a, b) => a + b, 0)
    const last = i + row.length >= rows.length
    if (w >= h) {                     // ряд колонкой у левого края
      const cw = last ? w : sum / h
      let cy = y
      row.forEach((r, j) => { const ch = j === row.length - 1 ? y + h - cy : areas[j] / cw; out.set(r.key, { x, y: cy, w: cw, h: ch }); cy += ch })
      x += cw
      w -= cw
    } else {                          // ряд строкой у верхнего края
      const rh = last ? h : sum / w
      let cx = x
      row.forEach((r, j) => { const cw = j === row.length - 1 ? x + w - cx : areas[j] / rh; out.set(r.key, { x: cx, y, w: cw, h: rh }); cx += cw })
      y += rh
      h -= rh
    }
    i += row.length
  }
  return out
}
