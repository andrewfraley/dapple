import { useEffect, useRef } from 'react'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import { useTheme } from '@mui/material/styles'

import { cssColor, ledColors } from './pattern.js'

const HEIGHT = 56

/**
 * The pattern as it will land on the physical run: one bar covering every
 * strand end to end, with a divider where one strand stops and the next
 * starts. Drawn from the same algorithm the server uses.
 *
 * `known` is false while no strand has answered — the bar then shows a
 * plausible length so the controls still preview, and says so.
 */
export default function PreviewStrip({ pattern, totalLeds, segments, known }) {
  const canvasRef = useRef(null)
  const theme = useTheme()

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ratio = window.devicePixelRatio || 1
    const width = canvas.clientWidth
    canvas.width = Math.max(1, Math.round(width * ratio))
    canvas.height = Math.round(HEIGHT * ratio)

    const context = canvas.getContext('2d')
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    context.clearRect(0, 0, width, HEIGHT)

    const leds = ledColors(pattern, totalLeds)
    if (!leds.length) {
      context.fillStyle = theme.palette.action.disabledBackground
      context.fillRect(0, 0, width, HEIGHT)
      return
    }

    // Sub-pixel LEDs are normal here (200+ LEDs in a phone-width strip). Snap
    // each LED to the pixel where the next one starts: no gaps, and no LED
    // painting over its neighbour.
    const step = width / leds.length
    leds.forEach((led, index) => {
      const start = Math.round(index * step)
      const end = Math.round((index + 1) * step)
      context.fillStyle = cssColor(led)
      context.fillRect(start, 0, Math.max(1, end - start), HEIGHT)
    })

    context.strokeStyle = theme.palette.background.paper
    context.lineWidth = 2
    segments.slice(1).forEach((segment) => {
      const x = segment.offset * step
      context.beginPath()
      context.moveTo(x, 0)
      context.lineTo(x, HEIGHT)
      context.stroke()
    })
  }, [pattern, totalLeds, segments, theme])

  return (
    <Box>
      <Box
        component="canvas"
        ref={canvasRef}
        sx={{
          display: 'block',
          width: '100%',
          height: HEIGHT,
          borderRadius: 1.5,
          border: 1,
          borderColor: 'divider',
        }}
      />
      <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
        {known
          ? `${totalLeds} LEDs across ${segments.length} strand${segments.length === 1 ? '' : 's'}: ` +
            segments.map((segment) => `${segment.name} (${segment.number_of_led})`).join(' · ')
          : `No strand has answered yet — previewing ${totalLeds} LEDs. Apply still works once one does.`}
      </Typography>
    </Box>
  )
}
