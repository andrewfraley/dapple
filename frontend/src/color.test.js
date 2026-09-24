import { describe, expect, it } from 'vitest'

import { HUE_STEPS, hueToRgb, lighten, lightness, rgbToHue } from './color.js'

describe('hue slider', () => {
  it('only reaches colors with one channel full and one off', () => {
    for (let hue = 0; hue < HUE_STEPS; hue++) {
      const rgb = hueToRgb(hue)
      expect(Math.max(...rgb)).toBe(255)
      expect(Math.min(...rgb)).toBe(0)
    }
  })

  it('reads back every position it can produce', () => {
    // A saved color that drifted by one step each load would creep around the wheel.
    for (let hue = 0; hue < HUE_STEPS; hue++) {
      expect(rgbToHue(hueToRgb(hue))).toBe(hue)
    }
  })

  it('places a dimmer saved color at its full-brightness hue', () => {
    expect(hueToRgb(rgbToHue([0, 200, 0]))).toEqual([0, 255, 0])
    expect(hueToRgb(rgbToHue([128, 0, 255]))).toEqual([128, 0, 255])
  })

  it('has no hue for black or grey', () => {
    expect(rgbToHue([0, 0, 0])).toBeNull()
    expect(rgbToHue([90, 90, 90])).toBeNull()
  })
})

describe('lighten slider', () => {
  it('stays in the FF range all the way to white', () => {
    for (const amount of [0, 1, 100, 254, 255]) {
      const rgb = lighten(hueToRgb(300), amount)
      expect(Math.max(...rgb)).toBe(255)
      expect(Math.min(...rgb)).toBe(amount)
    }
    expect(lighten([255, 0, 0], 255)).toEqual([255, 255, 255])
  })

  it('reads back how far a color was lightened', () => {
    for (const amount of [0, 64, 128, 255]) {
      expect(lightness(lighten(hueToRgb(900), amount))).toBe(amount)
    }
  })

  it('keeps the hue of a lightened color', () => {
    expect(rgbToHue(lighten(hueToRgb(0), 128))).toBe(0)
    expect(rgbToHue(lighten(hueToRgb(510), 200))).toBe(510)
  })
})
