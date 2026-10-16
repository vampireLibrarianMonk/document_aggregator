import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import type { AlignmentLibrary, LibraryState, Project } from '../../api/types'

const restoreProfile = vi.fn()
vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    api: { restoreProfile: (...a: unknown[]) => restoreProfile(...a) },
  }
})

import { RejectedShapes } from '../JsonAlignment'

const project = { id: 'p1', name: 'P' } as Project

function libraryWithRejected(prevState: 'approved' | 'provisional'): AlignmentLibrary {
  return {
    project_id: 'p1',
    target_title: 'game',
    approved: [],
    provisional: [],
    rejected: ['game__shape_1'],
    entries: [{
      profile_id: 'game__shape_1',
      state: 'rejected' as LibraryState,
      version: 1,
      mapped: {},
      needs_review: [],
      conflicts: [],
      candidates: {},
      source_paths: [],
      source_docs: [],
      source_doc_count: 0,
      rejection: {
        category: 'not_our_data',
        composed_text: 'Rejected — not our data.',
        rejected_at: '2025-01-01T12:00:00+00:00',
        rejected_by: 'reviewer',
        prev_state: prevState,
      },
    }],
  }
}

beforeEach(() => restoreProfile.mockReset())
afterEach(() => cleanup())

describe('RejectedShapes', () => {
  it('lists each rejected entry with its category and composed reason', () => {
    const { getByText } = render(
      <RejectedShapes project={project} library={libraryWithRejected('provisional')} onRestored={vi.fn()} />,
    )
    expect(getByText('game__shape_1')).toBeTruthy()
    expect(getByText('Not our data')).toBeTruthy()
    expect(getByText('Rejected — not our data.')).toBeTruthy()
  })

  it('restores only after the server confirms a non-rejected state', async () => {
    const onRestored = vi.fn()
    restoreProfile.mockResolvedValue({ state: 'provisional', profile_id: 'game__shape_1' })
    const { getByText } = render(
      <RejectedShapes project={project} library={libraryWithRejected('provisional')} onRestored={onRestored} />,
    )
    fireEvent.click(getByText('Restore'))
    await waitFor(() => expect(onRestored).toHaveBeenCalledTimes(1))
    expect(restoreProfile).toHaveBeenCalledWith('p1', 'game__shape_1')
  })

  it('an approved->rejected entry restoring with state=approved still refreshes', async () => {
    const onRestored = vi.fn()
    restoreProfile.mockResolvedValue({ state: 'approved', profile_id: 'game__shape_1' })
    const { getByText } = render(
      <RejectedShapes project={project} library={libraryWithRejected('approved')} onRestored={onRestored} />,
    )
    fireEvent.click(getByText('Restore'))
    await waitFor(() => expect(onRestored).toHaveBeenCalledTimes(1))
  })

  it('renders nothing when there are no rejected shapes', () => {
    const empty: AlignmentLibrary = {
      project_id: 'p1', target_title: 'game', approved: [], provisional: [],
      rejected: [], entries: [],
    }
    const { container } = render(
      <RejectedShapes project={project} library={empty} onRestored={vi.fn()} />,
    )
    expect(container.firstChild).toBeNull()
  })
})
