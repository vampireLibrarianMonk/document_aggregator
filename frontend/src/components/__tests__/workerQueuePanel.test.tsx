import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'
import type { JobStatus } from '../../api/types'

const getJobCounts = vi.fn()
vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    api: { getJobCounts: (...a: unknown[]) => getJobCounts(...a) },
  }
})

import { WorkerQueuePanel } from '../JsonAlignment'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function job(partial: Partial<JobStatus>): JobStatus {
  return {
    job_id: 'job_1', op: 'batch_align', state: 'processing', attempts: 1,
    worker_id: null, error: null, result: null, ...partial,
  }
}

describe('WorkerQueuePanel', () => {
  it('shows a STALLED warning when a job is pending but nothing is processing', async () => {
    getJobCounts.mockResolvedValue({ counts: { pending: 1 } })
    const active = job({ state: 'pending', worker_id: null })
    const { findByText, container } = render(<WorkerQueuePanel activeJob={active} />)
    // panel polls when a job is active even while collapsed
    await waitFor(() => expect(getJobCounts).toHaveBeenCalled())
    // the stalled-worker message appears
    await findByText(/worker may not be running/i)
    expect(container.textContent).toMatch(/Pending/)
  })

  it('shows a worker is processing when a worker id is present', async () => {
    getJobCounts.mockResolvedValue({ counts: { processing: 1 } })
    const active = job({ state: 'processing', worker_id: 'worker-123-0' })
    const { findByText } = render(<WorkerQueuePanel activeJob={active} />)
    await waitFor(() => expect(getJobCounts).toHaveBeenCalled())
    await findByText(/A worker is processing the queue/i)
    // active-job detail surfaces the worker id
    await findByText(/worker-123-0/)
  })

  it('renders live progress for the active job', async () => {
    getJobCounts.mockResolvedValue({ counts: { processing: 1 } })
    const active = job({ worker_id: 'w1', progress: { done: 2, total: 5 } })
    const { findByText } = render(<WorkerQueuePanel activeJob={active} />)
    await waitFor(() => expect(getJobCounts).toHaveBeenCalled())
    await findByText(/2 of 5 files · 40%/)
  })

  it('does not poll when idle and collapsed (no active job)', () => {
    getJobCounts.mockResolvedValue({ counts: {} })
    render(<WorkerQueuePanel activeJob={null} />)
    // collapsed + no active job => shouldPoll is false => no fetch
    expect(getJobCounts).not.toHaveBeenCalled()
  })

  it('surfaces an error from the counts endpoint without crashing', async () => {
    getJobCounts.mockRejectedValue(new Error('boom'))
    const active = job({ state: 'processing', worker_id: 'w1' })
    const { findByText } = render(<WorkerQueuePanel activeJob={active} />)
    await findByText(/boom/)
  })
})
