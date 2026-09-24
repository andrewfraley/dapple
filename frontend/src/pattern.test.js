// The JS port must produce exactly what app/pattern.py produces. Fixtures come
// from tests/test_pattern_parity.py; re-run pytest if this file's cases look
// stale.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import { interleave, ledColors, reduceWeights } from './pattern.js'

const fixtures = JSON.parse(
  readFileSync(
    fileURLToPath(new URL('../../tests/fixtures/pattern_fixtures.json', import.meta.url)),
    'utf8',
  ),
)

describe('parity with app/pattern.py', () => {
  it('has fixtures to check against', () => {
    expect(fixtures.length).toBeGreaterThan(0)
  })

  fixtures.forEach((fixture) => {
    it(`matches Python for ${fixture.label}`, () => {
      const leds = ledColors(fixture.pattern, fixture.num_leds, fixture.offset)
      expect(leds.map((led) => [...led])).toEqual(fixture.leds)
    })
  })
})

describe('building blocks', () => {
  it('reduces weights by their gcd', () => {
    expect(reduceWeights([80, 20])).toEqual([4, 1])
    expect(reduceWeights([7, 3])).toEqual([7, 3])
    expect(reduceWeights([])).toEqual([])
  })

  it('spreads 7:3 instead of clumping it', () => {
    const sequence = interleave([7, 3])
    expect(sequence.length).toBe(10)
    expect(sequence.filter((s) => s === 1).length).toBe(3)
    expect(sequence.join('')).not.toContain('000000')
  })
})
