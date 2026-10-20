import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'
import type { AlignmentLibrary, Project } from '../../api/types'

// Mock the API client used by ApprovalCards (it fetches the golden-field vocab
// on mount for the reject picker).
const getValueVocab = vi.fn()
vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    api: {
      getValueVocab: (...a: unknown[]) => getValueVocab(...a),
      approveProfile: vi.fn(),
      rejectProfile: vi.fn(),
      overrideFieldMapping: vi.fn(),
      changeValueVocab: vi.fn(),
    },
  }
})

import { ApprovalCards } from '../JsonAlignment'

const project = { id: 'p1', name: 'P' } as Project

/**
 * Two shapes whose *mapped* tables are identical (same 8 golden->source rows),
 * but one (shape_L8) carries two EXTRA source fields (`title`, `metascore`)
 * that are not mapped to any golden field. That extra-field difference is the
 * only thing distinguishing the shapes, and is why they are separate approvals.
 * The card must surface the unmapped source fields so the two look-alike cards
 * are visibly different. Mirrors the real project-7 L8_conflicting_candidates
 * vs clean_catalog case.
 */
function libraryWithLookalikeShapes(): AlignmentLibrary {
  const mapped = {
    name: 'name', releaseYear: 'releaseYear', developer: 'developer',
    publisher: 'publisher', platform: 'platform', genres: 'genres[]',
    criticScore: 'criticScore', ESRB: 'ESRB',
  }
  const basePaths = [
    'name', 'releaseYear', 'developer', 'publisher', 'platform',
    'genres[]', 'criticScore', 'ESRB',
  ].map((p) => ({ path: p, type: 'string' }))
  return {
    project_id: 'p1',
    target_title: 'game',
    approved: [],
    provisional: ['game__shape_L8', 'game__shape_clean'],
    rejected: [],
    entries: [
      {
        profile_id: 'game__shape_L8',
        state: 'provisional',
        version: 1,
        mapped,
        needs_review: [],
        conflicts: [],
        candidates: {},
        // The two extra fields that make this a distinct shape.
        source_paths: [...basePaths, { path: 'title', type: 'string' },
          { path: 'metascore', type: 'integer' }],
        source_docs: ['L8_conflicting_candidates.json'],
        source_doc_count: 1,
      },
      {
        profile_id: 'game__shape_clean',
        state: 'provisional',
        version: 1,
        mapped,
        needs_review: [],
        conflicts: [],
        candidates: {},
        source_paths: basePaths,
        source_docs: ['clean_catalog.json'],
        source_doc_count: 1,
      },
    ],
  }
}

beforeEach(() => {
  getValueVocab.mockReset()
  getValueVocab.mockResolvedValue({ project_id: 'p1', fields: [] })
})
afterEach(() => cleanup())

describe('ApprovalCards — unmapped source fields distinguish look-alike shapes', () => {
  it('shows the extra unmapped source fields on the shape that has them', async () => {
    const { getByText, container } = render(
      <ApprovalCards project={project} library={libraryWithLookalikeShapes()} onApproved={vi.fn()} />,
    )
    await waitFor(() => expect(getByText(/not\s+mapped to any golden field/i)).toBeTruthy())
    // The L8 shape's extra fields are named (each rendered in its own span,
    // the non-last one carries a trailing comma, so match by substring).
    expect(getByText((_t, el) => el?.textContent === 'title, ')).toBeTruthy()
    expect(getByText((_t, el) => el?.textContent === 'metascore')).toBeTruthy()
    // Exactly ONE of the two cards shows the unmapped note (clean_catalog does not).
    const notes = container.querySelectorAll('span')
    const noteCount = Array.from(notes).filter((n) =>
      /source field.*not\s+mapped to any golden field/i.test(n.textContent ?? ''),
    ).length
    expect(noteCount).toBe(1)
  })

  it('does not show an unmapped-fields note when every source field maps', () => {
    // A library where the only shape maps all its source fields.
    const lib = libraryWithLookalikeShapes()
    lib.entries = [lib.entries[1]] // just clean_catalog (no extra fields)
    lib.provisional = ['game__shape_clean']
    const { queryByText } = render(
      <ApprovalCards project={project} library={lib} onApproved={vi.fn()} />,
    )
    expect(queryByText(/not\s+mapped to any golden field/i)).toBeNull()
  })
})
