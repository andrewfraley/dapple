// Port of app/pattern.py, so the preview can be drawn without a round trip.
// tests/test_pattern_parity.py writes fixtures from the Python side and
// pattern.test.js checks this file reproduces them exactly — keep the two in
// step when you change either.

export const BLACK = [0, 0, 0, 0]

function gcd(a, b) {
  while (b) {
    ;[a, b] = [b, a % b]
  }
  return a
}

export function activeSlots(pattern) {
  return (pattern.slots || []).filter((slot) => slot.weight > 0)
}

/** Divide weights by their GCD, so [80, 20] becomes [4, 1]. */
export function reduceWeights(weights) {
  if (!weights.length) return []
  const divisor = weights.reduce((acc, weight) => gcd(acc, weight), 0)
  if (divisor <= 1) return [...weights]
  return weights.map((weight) => weight / divisor)
}

/**
 * Spread weighted colors as evenly as the counts allow.
 *
 * Each step hands the LED to whichever color is furthest behind its share
 * (largest-remainder apportionment); ties go to the earlier slot. So 7:3 comes
 * out as A B A A A B A A B A, never A A A A A A A B B B.
 */
export function interleave(weights) {
  const total = weights.reduce((sum, weight) => sum + weight, 0)
  if (total <= 0) return []
  const counts = weights.map(() => 0)
  const sequence = []
  for (let step = 1; step <= total; step += 1) {
    let best = 0
    let bestCredit = weights[0] * step - counts[0] * total
    for (let index = 1; index < weights.length; index += 1) {
      const credit = weights[index] * step - counts[index] * total
      if (credit > bestCredit) {
        best = index
        bestCredit = credit
      }
    }
    sequence.push(best)
    counts[best] += 1
  }
  return sequence
}

/** One repeating unit where each color owns weight * blockSize LEDs in a row. */
export function block(weights, blockSize) {
  const sequence = []
  weights.forEach((weight, index) => {
    for (let i = 0; i < weight * blockSize; i += 1) sequence.push(index)
  })
  return sequence
}

export function buildSequence(weights, layout = 'interleaved', blockSize = 1) {
  const reduced = reduceWeights(weights)
  if (layout === 'blocked') return block(reduced, Math.max(1, blockSize))
  return interleave(reduced)
}

/**
 * The RGBW value of every LED, in strand order.
 *
 * `offset` shifts the phase: applying it to a strand as the LED count of the
 * strands physically before it keeps the pattern unbroken across the join.
 */
export function ledColors(pattern, numLeds, offset = 0) {
  if (numLeds <= 0) return []
  const slots = activeSlots(pattern)
  if (!slots.length) return Array.from({ length: numLeds }, () => BLACK)
  const sequence = buildSequence(
    slots.map((slot) => slot.weight),
    pattern.layout,
    pattern.block_size,
  )
  if (!sequence.length) return Array.from({ length: numLeds }, () => BLACK)
  const colors = slots.map((slot) => slot.rgbw)
  const length = sequence.length
  return Array.from(
    { length: numLeds },
    (_, i) => colors[sequence[(i + offset) % length]],
  )
}

/** How an RGBW value looks on screen: the white channel washes the color out. */
export function cssColor([r, g, b, w]) {
  const mix = (channel) => Math.min(255, Math.round(channel + w * (1 - channel / 255) * 0.9))
  return `rgb(${mix(r)}, ${mix(g)}, ${mix(b)})`
}

export function rgbToHex([r, g, b]) {
  return `#${[r, g, b].map((c) => c.toString(16).padStart(2, '0')).join('')}`
}
