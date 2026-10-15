import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ApprovalCards } from '../JsonAlignment'
import { api } from '../../api/client'
import type { AlignmentLibrary, Project } from '../../api/types'

// A minimal json_alignment project and a library with one provisional shape, so
// ApprovalCards renders a single "Approve" button we can drive.
const project: Project = {
  id: 'p1',
  name: 'Alignment',
  created_at: '2024-01-01T00:00:00Z',
  kind: 'json_alignment',
  reject_below: 0.5,
}

const library: AlignmentLibrary = {
  project_id: 'p1',
  target_title: 'Golden',
  approved: [],
  provisional: ['shape_A'],
  entries: [
    {
      profile_id: 'shape_A',
      state: 'provisional',
      version: 1,
      mapped: { title: 'name' },
      needs_review: [],
      conflicts: [],
      candidates: {},
      source_paths: [{ path: 'name', type: 'string' }],
      source_docs: ['team_a.json'],
      source_doc_count: 1,
    },
  ],
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('cascade approve flow', () => {
  it('shows a confirmation dialog with the reassurance copy when cascade_count > 0; OK applies', async () => {
    const onApproved = vi.fn()
    const approveSpy = vi
      .spyOn(api, 'approveProfile')
      .mockImplementation((_projectId: string, _profileId: string, preview = false) => {
        if (preview) {
          return Promise.resolve({
            project_id: 'p1',
            profile_id: 'shape_A',
            state: 'provisional',
            preview: true,
            cascade_count: 3,
            cascade_doc_ids: ['b.json', 'c.json', 'd.json'],
          })
        }
        return Promise.resolve({
          project_id: 'p1',
          approved: 'shape_A',
          state: 'approved',
          cascade_applied: 3,
          cascade_doc_ids: ['b.json', 'c.json', 'd.json'],
        })
      })

    render(<ApprovalCards project={project} library={library} onApproved={onApproved} />)

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    // Preview fires first (preview=true), and the modal appears — but no apply yet.
    const dialog = await screen.findByRole('dialog')
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    expect(dialog.getAttribute('aria-labelledby')).toBeTruthy()
    expect(dialog.textContent).toContain('3')
    expect(dialog.textContent).toMatch(/only those 3 file/i)
    expect(dialog.textContent).toMatch(/not a blanket/i)
    // Still only the preview call so far — nothing applied, nothing refreshed.
    expect(approveSpy).toHaveBeenCalledTimes(1)
    expect(approveSpy).toHaveBeenCalledWith('p1', 'shape_A', true)
    expect(onApproved).not.toHaveBeenCalled()

    // OK applies (preview=false), refreshes, and closes the modal.
    fireEvent.click(screen.getByRole('button', { name: 'OK' }))
    await waitFor(() =>
      expect(approveSpy).toHaveBeenCalledWith('p1', 'shape_A', false),
    )
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(onApproved).toHaveBeenCalledTimes(1)
  })

  it('Cancel closes the dialog and applies nothing', async () => {
    const onApproved = vi.fn()
    const approveSpy = vi
      .spyOn(api, 'approveProfile')
      .mockImplementation((_projectId: string, _profileId: string, preview = false) => {
        if (preview) {
          return Promise.resolve({
            project_id: 'p1',
            state: 'provisional',
            preview: true,
            cascade_count: 2,
          })
        }
        return Promise.resolve({ project_id: 'p1', approved: 'shape_A', state: 'approved' })
      })

    render(<ApprovalCards project={project} library={library} onApproved={onApproved} />)
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await screen.findByRole('dialog')

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    // Only the preview call ever ran — no apply, no refresh.
    expect(approveSpy).toHaveBeenCalledTimes(1)
    expect(approveSpy).toHaveBeenCalledWith('p1', 'shape_A', true)
    expect(onApproved).not.toHaveBeenCalled()
  })

  it('with cascade_count === 0 no dialog appears and apply fires immediately', async () => {
    const onApproved = vi.fn()
    const approveSpy = vi
      .spyOn(api, 'approveProfile')
      .mockImplementation((_projectId: string, _profileId: string, preview = false) => {
        if (preview) {
          return Promise.resolve({
            project_id: 'p1',
            state: 'provisional',
            preview: true,
            cascade_count: 0,
          })
        }
        return Promise.resolve({ project_id: 'p1', approved: 'shape_A', state: 'approved' })
      })

    render(<ApprovalCards project={project} library={library} onApproved={onApproved} />)
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    // Apply (preview=false) runs without any dialog, then refreshes.
    await waitFor(() =>
      expect(approveSpy).toHaveBeenCalledWith('p1', 'shape_A', false),
    )
    expect(screen.queryByRole('dialog')).toBeNull()
    await waitFor(() => expect(onApproved).toHaveBeenCalledTimes(1))
  })
})
