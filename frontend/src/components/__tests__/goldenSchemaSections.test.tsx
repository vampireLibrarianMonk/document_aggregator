import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import type { Project, ValueVocab } from '../../api/types'

const getTargetSchema = vi.fn()
const getValueVocab = vi.fn()
const updateProject = vi.fn()
vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    api: {
      getTargetSchema: (...a: unknown[]) => getTargetSchema(...a),
      getValueVocab: (...a: unknown[]) => getValueVocab(...a),
      updateProject: (...a: unknown[]) => updateProject(...a),
    },
  }
})

import { GoldenSchemaPanel } from '../JsonAlignment'

const project = { id: 'p1', name: 'P', reject_below: 0.5 } as Project

function vocab(): ValueVocab {
  return {
    project_id: 'p1',
    fields: [
      { target: 'platform', description: '', builtin: ['Xbox'], learned: [], free_form: false, aliases: [] },
    ],
  }
}

/** Find a Section's disclosure header button by its title text. */
function sectionButton(title: string): HTMLButtonElement {
  const buttons = Array.from(document.querySelectorAll('button[aria-expanded]')) as HTMLButtonElement[]
  const btn = buttons.find((b) => b.textContent?.includes(title))
  if (!btn) throw new Error(`no section button with title ${title}`)
  return btn
}

beforeEach(() => {
  getTargetSchema.mockReset()
  getValueVocab.mockReset()
  updateProject.mockReset()
  getValueVocab.mockResolvedValue(vocab())
})
afterEach(() => cleanup())

describe('GoldenSchemaPanel collapsible sections', () => {
  // Test 8
  it('renders three sections: schema-save expanded, accepted-values + aliases collapsed', async () => {
    getTargetSchema.mockResolvedValue({ project_id: 'p1', target: { title: 'T', type: 'object', properties: {} } })
    render(<GoldenSchemaPanel project={project} onProjectChange={vi.fn()} />)
    await waitFor(() => expect(sectionButton('Golden target schema')).toBeTruthy())
    expect(sectionButton('Golden target schema').getAttribute('aria-expanded')).toBe('true')
    await waitFor(() => expect(sectionButton('Accepted values')).toBeTruthy())
    expect(sectionButton('Accepted values').getAttribute('aria-expanded')).toBe('false')
    expect(sectionButton('Value aliases').getAttribute('aria-expanded')).toBe('false')
  })

  // Test 9
  it('each section toggles independently (opening aliases does not close schema)', async () => {
    getTargetSchema.mockResolvedValue({ project_id: 'p1', target: { title: 'T', type: 'object', properties: {} } })
    render(<GoldenSchemaPanel project={project} onProjectChange={vi.fn()} />)
    await waitFor(() => expect(sectionButton('Value aliases')).toBeTruthy())
    fireEvent.click(sectionButton('Value aliases'))
    expect(sectionButton('Value aliases').getAttribute('aria-expanded')).toBe('true')
    // schema section stays open; accepted values stays closed
    expect(sectionButton('Golden target schema').getAttribute('aria-expanded')).toBe('true')
    expect(sectionButton('Accepted values').getAttribute('aria-expanded')).toBe('false')
  })

  // Test 10
  it('accepted-values + aliases sections appear only after a schema is loaded', async () => {
    getTargetSchema.mockRejectedValue(new Error('404'))  // no schema set
    render(<GoldenSchemaPanel project={project} onProjectChange={vi.fn()} />)
    // schema-save section is always present
    await waitFor(() => expect(sectionButton('Golden target schema')).toBeTruthy())
    // the other two are not rendered while unset
    const titles = Array.from(document.querySelectorAll('button[aria-expanded]'))
      .map((b) => b.textContent ?? '')
    expect(titles.some((t) => t.includes('Accepted values'))).toBe(false)
    expect(titles.some((t) => t.includes('Value aliases'))).toBe(false)
  })
})
