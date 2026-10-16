import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import type { Project, ValueVocab, VocabField } from '../../api/types'

const getTargetSchema = vi.fn()
const getValueVocab = vi.fn()
const changeValueVocab = vi.fn()
const updateProject = vi.fn()
vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    api: {
      getTargetSchema: (...a: unknown[]) => getTargetSchema(...a),
      getValueVocab: (...a: unknown[]) => getValueVocab(...a),
      changeValueVocab: (...a: unknown[]) => changeValueVocab(...a),
      updateProject: (...a: unknown[]) => updateProject(...a),
    },
  }
})

import { ValueAliasesEditor, GoldenSchemaPanel } from '../JsonAlignment'

const project = { id: 'p1', name: 'P', reject_below: 0.5 } as Project

/** A vocab fixture: `platform` has a built-in + a learned value, plus one alias
 *  targeting the learned value. `name` is free-form with an (inactive) alias. */
function vocab(opts: { freeFormName?: boolean } = {}): ValueVocab {
  return {
    project_id: 'p1',
    fields: [
      {
        target: 'platform', description: '',
        builtin: ['Windows PC', 'Xbox'],
        learned: [{ value: 'PlayStation', alias: null, added_by: 'r', added_at: '', note: '', example: '' }],
        free_form: false,
        aliases: [{ alias: 'PS5', value: 'PlayStation', added_by: 'r', added_at: '', note: 'specific' }],
      },
      {
        target: 'name', description: '',
        builtin: [],
        learned: [],
        free_form: opts.freeFormName ?? false,
        aliases: [{ alias: 'nick', value: 'Name', added_by: 'r', added_at: '', note: '' }],
      },
    ],
  }
}

/** Stateful wrapper so a mutation's returned vocab re-renders the editor. */
function Harness({ initial }: { initial: VocabField[] }) {
  const [f, setF] = useState<VocabField[]>(initial)
  return <ValueAliasesEditor project={project} fields={f} onVocabChanged={setF} />
}

beforeEach(() => {
  getTargetSchema.mockReset()
  getValueVocab.mockReset()
  changeValueVocab.mockReset()
  updateProject.mockReset()
})
afterEach(() => cleanup())

describe('ValueAliasesEditor', () => {
  // Test 1
  it('renders an alias row per field and shows existing alias → value chips', () => {
    const { getByText, getByLabelText } = render(
      <ValueAliasesEditor project={project} fields={vocab().fields} onVocabChanged={vi.fn()} />,
    )
    expect(getByText('platform')).toBeTruthy()
    expect(getByText('name')).toBeTruthy()
    // chip text "PS5 → PlayStation" (split across text nodes) — the remove
    // button carries the alias so assert via its aria-label too.
    expect(getByLabelText('Remove alias PS5')).toBeTruthy()
    expect(getByLabelText('Remove alias nick')).toBeTruthy()
  })

  // Test 2
  it('canonical <select> lists the field builtin ∪ learned values', () => {
    const { getByLabelText } = render(
      <ValueAliasesEditor project={project} fields={vocab().fields} onVocabChanged={vi.fn()} />,
    )
    const select = getByLabelText('Canonical value for platform') as HTMLSelectElement
    const opts = Array.from(select.options).map((o) => o.value).filter((v) => v !== '')
    expect(opts).toEqual(['Windows PC', 'Xbox', 'PlayStation'])
  })

  // Test 3
  it('add calls add_alias with {target, alias, value} and re-renders from returned vocab', async () => {
    const next = vocab()
    next.fields[0].aliases = [
      ...(next.fields[0].aliases ?? []),
      { alias: 'Win', value: 'Windows PC', added_by: 'r', added_at: '', note: '' },
    ]
    changeValueVocab.mockResolvedValue({ project_id: 'p1', change: {}, vocab: next.fields })
    const { getByLabelText, findByLabelText } = render(<Harness initial={vocab().fields} />)
    fireEvent.change(getByLabelText('Alias raw spelling for platform'), { target: { value: 'Win' } })
    fireEvent.change(getByLabelText('Canonical value for platform'), { target: { value: 'Windows PC' } })
    // the + button lives in the platform row's Add cell
    const row = getByLabelText('Alias raw spelling for platform').closest('tr') as HTMLTableRowElement
    fireEvent.click(within(row).getByText('+'))
    await waitFor(() => expect(changeValueVocab).toHaveBeenCalledWith('p1', {
      action: 'add_alias', target: 'platform', alias: 'Win', value: 'Windows PC',
    }))
    // re-rendered off the returned vocab: the new chip is present
    expect(await findByLabelText('Remove alias Win')).toBeTruthy()
  })

  // Test 4
  it('remove ✕ calls remove_alias with {target, alias}', async () => {
    const next = vocab()
    next.fields[0].aliases = []
    changeValueVocab.mockResolvedValue({ project_id: 'p1', change: {}, vocab: next.fields })
    const { getByLabelText } = render(<Harness initial={vocab().fields} />)
    fireEvent.click(getByLabelText('Remove alias PS5'))
    await waitFor(() => expect(changeValueVocab).toHaveBeenCalledWith('p1', {
      action: 'remove_alias', target: 'platform', alias: 'PS5',
    }))
  })

  // Test 5
  it('a server 422 renders inline and does not clear the input', async () => {
    changeValueVocab.mockRejectedValue(new Error('422: {"detail":"\'x\' is not an accepted value of \'platform\'."}'))
    const { getByLabelText, findByText } = render(<Harness initial={vocab().fields} />)
    const rawInput = getByLabelText('Alias raw spelling for platform') as HTMLInputElement
    fireEvent.change(rawInput, { target: { value: 'XP' } })
    fireEvent.change(getByLabelText('Canonical value for platform'), { target: { value: 'Windows PC' } })
    const row = rawInput.closest('tr') as HTMLTableRowElement
    fireEvent.click(within(row).getByText('+'))
    expect(await findByText(/is not an accepted value/)).toBeTruthy()
    expect(rawInput.value).toBe('XP')  // not cleared on error
  })

  // Test 6
  it('a free-form field shows inactive chips and disables add input + remove', () => {
    const { getByLabelText } = render(
      <ValueAliasesEditor project={project} fields={vocab({ freeFormName: true }).fields} onVocabChanged={vi.fn()} />,
    )
    // the free-form field's alias chip is marked inactive
    const inactive = document.querySelector('[aria-label="inactive while free-form is on"]')
    expect(inactive).not.toBeNull()
    const rawInput = getByLabelText('Alias raw spelling for name') as HTMLInputElement
    expect(rawInput.disabled).toBe(true)
    const removeBtn = getByLabelText('Remove alias nick') as HTMLButtonElement
    expect(removeBtn.disabled).toBe(true)
  })

  // Test 7
  it('client remap guard disables Add + shows a hint when raw already maps on the field', () => {
    const { getByLabelText, getByText } = render(
      <ValueAliasesEditor project={project} fields={vocab().fields} onVocabChanged={vi.fn()} />,
    )
    const rawInput = getByLabelText('Alias raw spelling for platform') as HTMLInputElement
    fireEvent.change(rawInput, { target: { value: 'PS5' } })  // already maps
    fireEvent.change(getByLabelText('Canonical value for platform'), { target: { value: 'Windows PC' } })
    const row = rawInput.closest('tr') as HTMLTableRowElement
    const addBtn = within(row).getByText('+') as HTMLButtonElement
    expect(addBtn.disabled).toBe(true)
    expect(getByText(/already maps to/)).toBeTruthy()
  })
})

// ---- Test 11: cross-component refresh via the lifted shared vocab state ----

/** A schema + vocab fixture for GoldenSchemaPanel. `platform` has a learned
 *  value `PlayStation` and an alias `PS5 → PlayStation`. */
function panelVocab(): ValueVocab {
  return {
    project_id: 'p1',
    fields: [
      {
        target: 'platform', description: '',
        builtin: ['Windows PC'],
        learned: [{ value: 'PlayStation', alias: null, added_by: 'r', added_at: '', note: '', example: '' }],
        free_form: false,
        aliases: [{ alias: 'PS5', value: 'PlayStation', added_by: 'r', added_at: '', note: '' }],
      },
    ],
  }
}

describe('GoldenSchemaPanel lifted vocab (cross-component refresh)', () => {
  it('removing a learned value drops its dependent alias chip from the aliases section', async () => {
    getTargetSchema.mockResolvedValue({ project_id: 'p1', target: { title: 'T', type: 'object', properties: {} } })
    getValueVocab.mockResolvedValue(panelVocab())
    // removing PlayStation cascades: the returned vocab has no learned value and
    // no PS5 alias anymore.
    const cascaded = panelVocab()
    cascaded.fields[0].learned = []
    cascaded.fields[0].aliases = []
    changeValueVocab.mockResolvedValue({ project_id: 'p1', change: {}, vocab: cascaded.fields })

    const { findByText, getByText, getByLabelText, queryByLabelText } = render(
      <GoldenSchemaPanel project={project} onProjectChange={vi.fn()} />,
    )
    // sections render once the schema is loaded; they start collapsed, so open
    // both Accepted values and Value aliases.
    fireEvent.click(await findByText('Accepted values'))
    fireEvent.click(getByText('Value aliases'))
    // both the accepted-value chip (remove) and the alias chip are now visible
    await waitFor(() => expect(getByLabelText('Remove alias PS5')).toBeTruthy())
    fireEvent.click(getByLabelText('Remove PlayStation'))
    await waitFor(() => expect(changeValueVocab).toHaveBeenCalledWith('p1', expect.objectContaining({
      action: 'remove', target: 'platform', value: 'PlayStation',
    })))
    // the aliases section (sharing the lifted vocab) no longer shows PS5
    await waitFor(() => expect(queryByLabelText('Remove alias PS5')).toBeNull())
  })
})
