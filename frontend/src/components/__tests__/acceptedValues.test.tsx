import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import type { Project, ValueVocab } from '../../api/types'

const getValueVocab = vi.fn()
const changeValueVocab = vi.fn()
vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    api: {
      getValueVocab: (...a: unknown[]) => getValueVocab(...a),
      changeValueVocab: (...a: unknown[]) => changeValueVocab(...a),
    },
  }
})

import { AcceptedValuesEditor } from '../JsonAlignment'

const project = { id: 'p1', name: 'P' } as Project

function vocab(freeForm: boolean): ValueVocab {
  return {
    project_id: 'p1',
    fields: [
      { target: 'name', description: '', builtin: [], learned: [], free_form: false },
      {
        target: 'platform', description: '',
        builtin: ['Windows PC'],
        learned: [{ value: 'Linux', alias: null, added_by: 'r', added_at: '', note: '', example: '' }],
        free_form: freeForm,
      },
    ],
  }
}

beforeEach(() => {
  getValueVocab.mockReset()
  changeValueVocab.mockReset()
})
afterEach(() => cleanup())

describe('AcceptedValuesEditor', () => {
  it('renders a row for every field, including a no-builtin/no-learned one', async () => {
    getValueVocab.mockResolvedValue(vocab(false))
    const { findByText } = render(<AcceptedValuesEditor project={project} />)
    expect(await findByText('name')).toBeTruthy()
    expect(await findByText('platform')).toBeTruthy()
  })

  it('toggling free-form calls set_free_form and greys values + disables add', async () => {
    getValueVocab.mockResolvedValue(vocab(false))
    // server confirms free-form on
    changeValueVocab.mockResolvedValue({ project_id: 'p1', change: {}, vocab: vocab(true).fields })
    const { findByLabelText } = render(<AcceptedValuesEditor project={project} />)
    const toggle = await findByLabelText('Mark platform free-form') as HTMLInputElement
    fireEvent.click(toggle)
    await waitFor(() => expect(changeValueVocab).toHaveBeenCalledWith('p1', expect.objectContaining({
      action: 'set_free_form', target: 'platform', free_form: true,
    })))
    // the add input for platform is disabled while free-form is on
    const addInput = await findByLabelText('Accept a value for platform') as HTMLInputElement
    await waitFor(() => expect(addInput.disabled).toBe(true))
    // learned value is marked inactive
    const inactive = document.querySelector('[aria-label="inactive while free-form is on"]')
    expect(inactive).not.toBeNull()
  })

  it('turning free-form off re-enables the add input', async () => {
    getValueVocab.mockResolvedValue(vocab(true))
    changeValueVocab.mockResolvedValue({ project_id: 'p1', change: {}, vocab: vocab(false).fields })
    const { findByLabelText } = render(<AcceptedValuesEditor project={project} />)
    const toggle = await findByLabelText('Mark platform free-form') as HTMLInputElement
    expect(toggle.checked).toBe(true)
    fireEvent.click(toggle)
    await waitFor(() => expect(changeValueVocab).toHaveBeenCalledWith('p1', expect.objectContaining({
      action: 'clear_free_form', target: 'platform', free_form: false,
    })))
    const addInput = await findByLabelText('Accept a value for platform') as HTMLInputElement
    await waitFor(() => expect(addInput.disabled).toBe(false))
  })
})
