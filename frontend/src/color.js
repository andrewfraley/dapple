// Every color the hue slider can reach has one channel at 255 and one at 0:
// full brightness, full saturation. Lighter shades come from the strand's
// white LED rather than from mixing R, G and B up toward white, which the
// strands render as a muddy, off-hue tint.

/** Six 255-step legs around the color wheel, so every 8-bit hue has a stop. */
export const HUE_STEPS = 6 * 255

/** The full-saturation color at `hue`, a whole number in [0, HUE_STEPS). */
export function hueToRgb(hue) {
  const h = ((Math.round(hue) % HUE_STEPS) + HUE_STEPS) % HUE_STEPS
  const leg = Math.floor(h / 255)
  const t = h % 255
  return [
    [255, t, 0],
    [255 - t, 255, 0],
    [0, 255, t],
    [0, 255 - t, 255],
    [t, 0, 255],
    [255, 0, 255 - t],
  ][leg]
}

/**
 * The slider position closest to `[r, g, b]`, or null for greys and black,
 * which have no hue. Round-trips exactly for anything hueToRgb returns.
 */
export function rgbToHue([r, g, b]) {
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  if (max === min) return null
  const rise = (x) => Math.round(((x - min) / (max - min)) * 255)
  if (r === max && b === min) return rise(g)
  if (g === max && b === min) return 255 + (255 - rise(r))
  if (g === max && r === min) return 2 * 255 + rise(b)
  if (b === max && r === min) return 3 * 255 + (255 - rise(g))
  if (b === max && g === min) return 4 * 255 + rise(r)
  return 5 * 255 + (255 - rise(b))
}

/**
 * Raise every channel of `rgb` toward 255 by `amount` (0–255). This is how an
 * RGB strand, with no white LED, gets pastels: the full channel stays full and
 * the others rise, so the result is still in the FF range and never dimmer.
 */
export function lighten(rgb, amount) {
  return rgb.map((c) => Math.round(c + (amount * (255 - c)) / 255))
}

/** How far `lighten` raised `rgb`, read off its lowest channel. */
export function lightness([r, g, b]) {
  const max = Math.max(r, g, b)
  return max ? Math.round((Math.min(r, g, b) / max) * 255) : 0
}
