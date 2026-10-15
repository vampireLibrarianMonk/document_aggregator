import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { batchProgressLabel } from '../JsonAlignment'

afterEach(() => cleanup())

describe('batchProgressLabel', () => {
  it('renders "N of M files · P%" with rounded percent', () => {
    expect(batchProgressLabel({ done: 1, total: 3 })).toBe('1 of 3 files · 33%')
    expect(batchProgressLabel({ done: 2, total: 3 })).toBe('2 of 3 files · 67%')
    expect(batchProgressLabel({ done: 3, total: 3 })).toBe('3 of 3 files · 100%')
  })

  it('falls back to "processing…" when progress is absent or total is 0', () => {
    expect(batchProgressLabel(null)).toBe('processing…')
    expect(batchProgressLabel({ done: 0, total: 0 })).toBe('processing…')
  })

  it('renders distinct intermediate frames for a 4-file run', () => {
    // File-granular progress: each file completion is its own visible frame,
    // not a single 0->100% jump.
    expect(batchProgressLabel({ done: 1, total: 4 })).toBe('1 of 4 files · 25%')
    expect(batchProgressLabel({ done: 2, total: 4 })).toBe('2 of 4 files · 50%')
    expect(batchProgressLabel({ done: 3, total: 4 })).toBe('3 of 4 files · 75%')
    expect(batchProgressLabel({ done: 4, total: 4 })).toBe('4 of 4 files · 100%')
  })
})

describe('batch progress indicator slot', () => {
  // Mirrors the exact node rendered in the "Upload & convert" header row of
  // BatchConvertPanel: a text-only status region, no bar and no spinner.
  function Indicator({ progress }: { progress: { done: number; total: number } | null }) {
    return (
      <span className="muted small" role="status" aria-live="polite">
        {batchProgressLabel(progress)}
      </span>
    )
  }

  it('exposes the live text through a role=status / aria-live region with no bar or spinner', () => {
    const { container, getByRole } = render(<Indicator progress={{ done: 1, total: 3 }} />)
    const status = getByRole('status')
    expect(status.getAttribute('aria-live')).toBe('polite')
    expect(status.textContent).toBe('1 of 3 files · 33%')
    // Text-only: no <progress>, no role="progressbar", no spinner element.
    expect(container.querySelector('progress')).toBeNull()
    expect(container.querySelector('[role="progressbar"]')).toBeNull()
    expect(container.querySelector('.spinner')).toBeNull()
  })
})
