import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'

const dashboardDir = new URL('../dashboard/', import.meta.url)
const manifest = JSON.parse(
  readFileSync(new URL('../dashboard/manifest.json', import.meta.url), 'utf8')
)

test('backend-only dashboard manifest hides the tab and ships a valid entry', () => {
  assert.equal(manifest.tab?.hidden, true)
  assert.equal(manifest.css, undefined)
  assert.ok(existsSync(new URL(manifest.entry, dashboardDir)), `missing entry: ${manifest.entry}`)
  assert.ok(existsSync(new URL(manifest.api, dashboardDir)), `missing API: ${manifest.api}`)
})
