import { useEffect, useRef, useState } from 'react'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'

import { cssColor, ledColors } from './pattern.js'

// The panel is always dark, whatever the theme: lit bulbs only read as lights
// against the dark, and it matches the logo.
const PANEL = '#1c1a2b'
const UNLIT = '#34304a'
const LABEL = '#9e99b8'

const TARGET_PITCH = 12
const PADDING = 12
const LABEL_HEIGHT = 18
const STRAND_GAP = 10

/**
 * One dot per LED, wrapped into rows, with each strand in its own block so the
 * seam between strands is visible. The pattern still runs continuously across
 * the blocks, exactly as the server lays it out. Drawn from the same algorithm
 * the server uses.
 *
 * `known` is false while no strand has answered — the grid then shows a
 * plausible length so the controls still preview, and says so.
 */
export default function PreviewStrip({ pattern, totalLeds, segments, known }) {
  const canvasRef = useRef(null)
  const [width, setWidth] = useState(0)

  // The row length depends on the width, so a resize has to redraw, not just
  // stretch the bitmap.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [])

  const labelled = segments.length > 1
  const inner = Math.max(0, width - PADDING * 2)
  const columns = Math.max(1, Math.floor(inner / TARGET_PITCH))
  const pitch = inner / columns
  const blocks = segments.map((segment) => Math.ceil(segment.number_of_led / columns))
  const height =
    PADDING * 2 +
    blocks.reduce((sum, rows) => sum + rows * pitch, 0) +
    (labelled ? segments.length * LABEL_HEIGHT : 0) +
    STRAND_GAP * (segments.length - 1)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !width) return
    const ratio = window.devicePixelRatio || 1
    canvas.width = Math.max(1, Math.round(width * ratio))
    canvas.height = Math.round(height * ratio)

    const context = canvas.getContext('2d')
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    context.fillStyle = PANEL
    context.fillRect(0, 0, width, height)

    const leds = ledColors(pattern, totalLeds)
    const radius = pitch * 0.34
    context.font = '11px system-ui, sans-serif'
    context.textBaseline = 'middle'

    let top = PADDING
    segments.forEach((segment, index) => {
      if (labelled) {
        context.shadowBlur = 0
        context.fillStyle = LABEL
        context.fillText(segment.name, PADDING, top + LABEL_HEIGHT / 2 - 2)
        top += LABEL_HEIGHT
      }
      for (let led = 0; led < segment.number_of_led; led++) {
        const color = leds[segment.offset + led]
        const lit = color && color.some((channel) => channel > 0)
        const x = PADDING + (led % columns + 0.5) * pitch
        const y = top + (Math.floor(led / columns) + 0.5) * pitch
        const fill = lit ? cssColor(color) : UNLIT
        context.shadowColor = fill
        context.shadowBlur = lit ? pitch * 0.6 : 0
        context.fillStyle = fill
        context.beginPath()
        context.arc(x, y, radius, 0, Math.PI * 2)
        context.fill()
      }
      top += blocks[index] * pitch + STRAND_GAP
    })
  }, [pattern, totalLeds, segments, width, height, pitch, columns, blocks, labelled])

  return (
    <Box>
      <Box
        component="canvas"
        ref={canvasRef}
        sx={{
          display: 'block',
          width: '100%',
          height,
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
