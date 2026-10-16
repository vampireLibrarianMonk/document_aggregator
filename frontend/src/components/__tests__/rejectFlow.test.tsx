import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import type { AlignmentLibrary, Project } from '../../api/types'

// Mock the API client used by ApprovalCards.
const rejectProfile = vi.fn()
const getValueVocab = vi.fn()
vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    api: {
      rejectProfile: (...a: unknown[]) => rejectProfile(...a),
      getValueVocab: (...a: unknown[]) => getValueVocab(...a),
      approveProfile: vi.fn(),
      overrideFieldMapping: vi.fn(),
      changeValueVocab: vi.fn(),
    },
  }
})

import { ApprovalCards } from '../JsonAlignment'

const project = { id: 'p1', name: 'P' } as Project

function libraryWithProvisional(): AlignmentLibrary {
  return {
    project_id: 'p1',
    target_title: 'game',
    approved: [],
    provisional: ['game__shape_1'],
    rejected: [],
    entries: [{
      profile_id: 'game__shape_1',
      state: 'provisional',
      version: 1,
      mapped: { name: 'name' },
      needs_review: ['platform'],
      conflicts: [],
      candidates: {},
      source_paths: [],
      source_docs: [],
      source_doc_count: 0,
    }],
  }
}

beforeEach(() => {
  rejectProfile.mockReset()
  getValueVocab.mockReset()
  getValueVocab.mockResolvedValue({
    project_id: 'p1',
    fields: [
      { target: 'name', description: '', builtin: [], learned: [], free_form: false },
      { target: 'platform', description: '', builtin: [], learned: [], free_form: false },
      { target: 'genres', description: '', builtin: [], learned: [], free_form: false },
    ],
  })
})
afterEach(() => cleanup())

describe('ApprovalCards reject flow', () => {
  it('submit stays disabled for other until the note is informative', async () => {
    const onApproved = vi.fn()
    const { getByText, getByRole, findByLabelText } = render(
      <ApprovalCards project={project} library={libraryWithProvisional()} onApproved={onApproved} />,
    )
    fireEvent.click(getByRole('button', { name: 'Reject' }))
    const cat = await findByLabelText('Reason') as HTMLSelectElement
    fireEvent.change(cat, { target: { value: 'other' } })
    const confirm = getByText('Confirm reject') as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    // 9 non-space chars -> still disabled
    const note = document.getElementById('reject-note-game__shape_1') as HTMLTextAreaElement
    fireEvent.change(note, { target: { value: 'too short' } })
    expect(confirm.disabled).toBe(true)
    // 10 non-space chars -> enabled
    fireEvent.change(note, { target: { value: 'this is fine' } })
    expect(confirm.disabled).toBe(false)
  })

  it('wrong_mapping requires at least one checked field', async () => {
    const { getByText, getByRole, findByLabelText } = render(
      <ApprovalCards project={project} library={libraryWithProvisional()} onApproved={vi.fn()} />,
    )
    fireEvent.click(getByRole('button', { name: 'Reject' }))
    const cat = await findByLabelText('Reason') as HTMLSelectElement
    fireEvent.change(cat, { target: { value: 'wrong_mapping' } })
    const confirm = getByText('Confirm reject') as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    // the field picker sources the FULL golden-field list from getValueVocab
    const genres = await findByLabelText('mismapped field genres') as HTMLInputElement
    fireEvent.click(genres)
    expect(confirm.disabled).toBe(false)
  })

  it('a successful reject (state=rejected) triggers the refresh', async () => {
    const onApproved = vi.fn()
    rejectProfile.mockResolvedValue({ state: 'rejected', profile_id: 'game__shape_1' })
    const { getByText, getByRole } = render(
      <ApprovalCards project={project} library={libraryWithProvisional()} onApproved={onApproved} />,
    )
    fireEvent.click(getByRole('button', { name: 'Reject' }))
    fireEvent.click(getByText('Confirm reject'))  // not_our_data is valid by default
    await waitFor(() => expect(onApproved).toHaveBeenCalledTimes(1))
    expect(rejectProfile).toHaveBeenCalledWith('p1', expect.objectContaining({
      profile_id: 'game__shape_1', category: 'not_our_data',
    }))
  })

  it('a 422 keeps the form open and shows the parsed detail', async () => {
    const onApproved = vi.fn()
    rejectProfile.mockRejectedValue(new Error('422: {"detail":"category \'other\' requires an informative note"}'))
    const { getByText, getByRole, findByRole } = render(
      <ApprovalCards project={project} library={libraryWithProvisional()} onApproved={onApproved} />,
    )
    fireEvent.click(getByRole('button', { name: 'Reject' }))
    fireEvent.click(getByText('Confirm reject'))
    const alert = await findByRole('alert')
    expect(alert.textContent).toContain('informative note')
    expect(onApproved).not.toHaveBeenCalled()
    // form is still open (Confirm reject still present)
    expect(getByText('Confirm reject')).toBeTruthy()
  })
})
