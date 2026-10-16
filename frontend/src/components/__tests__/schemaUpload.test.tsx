import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import type { Project } from '../../api/types'

const getTargetSchema = vi.fn()
const setTargetSchema = vi.fn()
const getValueVocab = vi.fn()
vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    api: {
      getTargetSchema: (...a: unknown[]) => getTargetSchema(...a),
      setTargetSchema: (...a: unknown[]) => setTargetSchema(...a),
      getValueVocab: (...a: unknown[]) => getValueVocab(...a),
      updateProject: vi.fn(),
    },
  }
})

import { GoldenSchemaPanel } from '../JsonAlignment'

const project = { id: 'p1', name: 'P', reject_below: 0.5 } as Project

// jsdom's File.text() is not always implemented; provide a File-like object.
function jsonFile(name: string, body: string): File {
  const f = new File([body], name, { type: 'application/json' })
  // Ensure .text() returns our body deterministically.
  Object.defineProperty(f, 'text', { value: () => Promise.resolve(body) })
  return f
}

const SCHEMA_A = '{"title":"A","type":"object","properties":{"name":{"type":"string"}}}'
const SCHEMA_B = '{"title":"B","type":"object","properties":{"id":{"type":"string"}}}'

beforeEach(() => {
  getTargetSchema.mockReset()
  setTargetSchema.mockReset()
  getValueVocab.mockReset()
  getTargetSchema.mockRejectedValue(new Error('404'))  // no schema yet
  getValueVocab.mockResolvedValue({ project_id: 'p1', fields: [] })
  setTargetSchema.mockImplementation((_pid: string, parsed: { title?: string }) =>
    Promise.resolve({ project_id: 'p1', title: parsed.title ?? 't', fields: 1, required: [] }))
})
afterEach(() => cleanup())

describe('GoldenSchemaPanel upload', () => {
  it('picking a valid .json file populates the textarea AND saves the file content on the first pick', async () => {
    const { getByLabelText } = render(
      <GoldenSchemaPanel project={project} onProjectChange={vi.fn()} />,
    )
    const input = getByLabelText('Upload schema file') as HTMLInputElement
    fireEvent.change(input, { target: { files: [jsonFile('a.json', SCHEMA_A)] } })
    await waitFor(() => expect(setTargetSchema).toHaveBeenCalledTimes(1))
    // saved the FIRST file's parsed content (not stale '' state)
    expect(setTargetSchema).toHaveBeenCalledWith('p1', JSON.parse(SCHEMA_A))
    const textarea = document.getElementById('golden-schema') as HTMLTextAreaElement
    expect(textarea.value).toBe(SCHEMA_A)
  })

  it('a second different file saves the second file content', async () => {
    const { getByLabelText } = render(
      <GoldenSchemaPanel project={project} onProjectChange={vi.fn()} />,
    )
    const input = getByLabelText('Upload schema file') as HTMLInputElement
    fireEvent.change(input, { target: { files: [jsonFile('a.json', SCHEMA_A)] } })
    await waitFor(() => expect(setTargetSchema).toHaveBeenCalledTimes(1))
    fireEvent.change(input, { target: { files: [jsonFile('b.json', SCHEMA_B)] } })
    await waitFor(() => expect(setTargetSchema).toHaveBeenCalledTimes(2))
    expect(setTargetSchema).toHaveBeenLastCalledWith('p1', JSON.parse(SCHEMA_B))
  })

  it('an invalid file shows "Not valid JSON" without calling the API', async () => {
    const { getByLabelText, findByText } = render(
      <GoldenSchemaPanel project={project} onProjectChange={vi.fn()} />,
    )
    const input = getByLabelText('Upload schema file') as HTMLInputElement
    fireEvent.change(input, { target: { files: [jsonFile('bad.json', 'not json {')] } })
    expect(await findByText('Not valid JSON')).toBeTruthy()
    expect(setTargetSchema).not.toHaveBeenCalled()
  })

  it('resets the input value so re-selecting the same file re-fires onChange', async () => {
    const { getByLabelText } = render(
      <GoldenSchemaPanel project={project} onProjectChange={vi.fn()} />,
    )
    const input = getByLabelText('Upload schema file') as HTMLInputElement
    fireEvent.change(input, { target: { files: [jsonFile('a.json', SCHEMA_A)] } })
    await waitFor(() => expect(setTargetSchema).toHaveBeenCalledTimes(1))
    expect(input.value).toBe('')  // reset after pick
  })
})
