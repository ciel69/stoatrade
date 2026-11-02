import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyText } from './copy'

describe('copyText', () => {
  afterEach(() => vi.unstubAllGlobals())
  it('использует буфер обмена браузера', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    expect(await copyText('./tb exec resume')).toBe(true)
    expect(writeText).toHaveBeenCalledWith('./tb exec resume')
  })
  it('запрет буфера: запасной способ; полный отказ даёт false, а не исключение', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
    document.execCommand = vi.fn().mockReturnValue(true)
    expect(await copyText('x')).toBe(true)
    document.execCommand = vi.fn().mockImplementation(() => { throw new Error('no') })
    expect(await copyText('x')).toBe(false)
  })
})
