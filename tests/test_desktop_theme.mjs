import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const pluginSource = await readFile(new URL('../desktop/plugin.js', import.meta.url), 'utf8')

test('desktop memory styles use the active theme instead of fixed light colors', () => {
  assert.match(pluginSource, /var\(--foreground/)
  assert.match(pluginSource, /var\(--card/)
  assert.doesNotMatch(pluginSource, /#[0-9a-f]{3,8}\b/i)
  assert.doesNotMatch(pluginSource, /rgba?\(\s*\d/i)
})
