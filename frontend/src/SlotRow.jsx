import { useId, useState } from 'react'
import Box from '@mui/material/Box'
import ButtonBase from '@mui/material/ButtonBase'
import Checkbox from '@mui/material/Checkbox'
import Collapse from '@mui/material/Collapse'
import FormControlLabel from '@mui/material/FormControlLabel'
import IconButton from '@mui/material/IconButton'
import Slider from '@mui/material/Slider'
import Stack from '@mui/material/Stack'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import useMediaQuery from '@mui/material/useMediaQuery'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward'
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'

import { HUE_STEPS, hueToRgb, lighten, lightness, rgbToHue } from './color.js'
import { cssColor, rgbToHex } from './pattern.js'

const RAINBOW = 'linear-gradient(to right, #f00, #ff0, #0f0, #0ff, #00f, #f0f, #f00)'

// 44px is the smallest comfortable touch target; MUI's small icon buttons are 34.
const TOUCH = { minWidth: 44, minHeight: 44 }

const LABEL = { display: 'flex', alignItems: 'center', gap: 1.5, flex: 1, minWidth: 0 }

/**
 * One color in the pattern: a full-strength hue, how much lighter to make it,
 * and its share of the LEDs.
 *
 * RGBW strands lighten with their white LED. RGB strands have none, so they
 * lighten by raising the other two channels instead. Either way the picked
 * color keeps one channel at 255: dimming is the brightness slider's job.
 */
export default function SlotRow({
  slot,
  index,
  share,
  hasWhite,
  open,
  onToggle,
  canRemove,
  canMoveUp,
  canMoveDown,
  onChange,
  onRemove,
  onMove,
}) {
  const [r, g, b, w] = slot.rgbw
  const setRgbw = (rgbw) => onChange({ ...slot, rgbw })
  const lift = hasWhite ? w : lightness([r, g, b])
  const compose = (hue, amount) =>
    hasWhite ? [...hueToRgb(hue), amount] : [...lighten(hueToRgb(hue), amount), 0]

  // Lightening leaves fewer distinct colors than there are hue steps, so
  // reading the hue back off the color can land a step or two from where the
  // thumb was dragged. Trust the last dragged hue while it still explains the
  // color; it also holds the hue while the color is pure white.
  const [lastHue, setLastHue] = useState(() => rgbToHue([r, g, b]) ?? 0)
  const explains = compose(lastHue, lift).every((c, i) => c === slot.rgbw[i])
  const hue = explains ? lastHue : (rgbToHue([r, g, b]) ?? lastHue)
  const whiteOnly = hasWhite && r === 0 && g === 0 && b === 0
  const name = `color ${index + 1}`
  const controlsId = useId()
  // A few colors' worth of stacked sliders fill a phone screen, so there each
  // color folds down to its swatch. Wider screens have room to show them all.
  const collapsible = useMediaQuery((theme) => theme.breakpoints.down('sm'))

  const label = (
    <>
      <Box
        sx={{
          width: 36,
          height: 36,
          borderRadius: '50%',
          border: 1,
          borderColor: 'grey.600',
          backgroundColor: cssColor(slot.rgbw),
          flexShrink: 0,
        }}
        title={hasWhite ? 'How this color will look with its white LED mixed in' : undefined}
      />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="body2">Color {index + 1}</Typography>
        <Typography variant="caption" color="text.secondary">
          {share}% of the LEDs
        </Typography>
      </Box>
    </>
  )

  return (
    <Box sx={{ py: 1.5, borderBottom: 1, borderColor: 'divider' }}>
      <Stack direction="row" spacing={1.5} alignItems="center">
        {collapsible ? (
          // The whole swatch-and-label area is the toggle, not just the chevron.
          <ButtonBase
            onClick={onToggle}
            aria-expanded={open}
            aria-controls={controlsId}
            sx={{ ...LABEL, minHeight: 44, borderRadius: 1, textAlign: 'left' }}
          >
            {label}
            <ExpandMoreIcon
              sx={{
                color: 'text.secondary',
                transition: 'transform 150ms',
                transform: open ? 'rotate(180deg)' : 'none',
              }}
            />
          </ButtonBase>
        ) : (
          <Box sx={LABEL}>{label}</Box>
        )}
        <Tooltip describeChild title="Move up">
          <span>
            <IconButton
              sx={TOUCH}
              disabled={!canMoveUp}
              onClick={() => onMove(-1)}
              aria-label={`Move ${name} up`}
            >
              <ArrowUpwardIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip describeChild title="Move down">
          <span>
            <IconButton
              sx={TOUCH}
              disabled={!canMoveDown}
              onClick={() => onMove(1)}
              aria-label={`Move ${name} down`}
            >
              <ArrowDownwardIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip describeChild title="Remove color">
          <span>
            <IconButton
              sx={TOUCH}
              disabled={!canRemove}
              onClick={onRemove}
              aria-label={`Remove ${name}`}
            >
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Stack>

      <Collapse in={!collapsible || open} id={controlsId}>
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', md: '2fr 1fr 1fr' },
            columnGap: 3,
            rowGap: 0.5,
            mt: 1,
            // Keep thumbs clear of the card edge so they stay grabbable at the ends.
            px: 1,
          }}
        >
          <Box>
            <Stack direction="row" justifyContent="space-between" alignItems="center">
              <Typography variant="caption">Color</Typography>
              {hasWhite && (
                <FormControlLabel
                  sx={{ mr: 0 }}
                  control={
                    <Checkbox
                      size="small"
                      checked={whiteOnly}
                      onChange={(event) =>
                        setRgbw(
                          event.target.checked
                            ? // White only with the white LED off would be an unlit LED.
                              [0, 0, 0, w || 255]
                            : compose(hue, w),
                        )
                      }
                    />
                  }
                  label={<Typography variant="caption">White only</Typography>}
                />
              )}
            </Stack>
            <Slider
              value={hue}
              min={0}
              max={HUE_STEPS - 1}
              disabled={whiteOnly}
              onChange={(_event, value) => {
                setLastHue(value)
                setRgbw(compose(value, lift))
              }}
              aria-label={`Hue of ${name}`}
              getAriaValueText={() => rgbToHex(hueToRgb(hue))}
              sx={{
                '& .MuiSlider-rail': {
                  background: RAINBOW,
                  opacity: whiteOnly ? 0.3 : 1,
                  height: 10,
                },
                '& .MuiSlider-track': { display: 'none' },
                '& .MuiSlider-thumb': {
                  width: 24,
                  height: 24,
                  backgroundColor: rgbToHex(hueToRgb(hue)),
                  border: 2,
                  borderColor: 'common.white',
                },
              }}
            />
          </Box>

          <LabeledSlider
            label={hasWhite ? `White — ${w}` : `Lighten — ${lift}`}
            value={lift}
            max={255}
            onChange={(value) => setRgbw(whiteOnly ? [0, 0, 0, value] : compose(hue, value))}
            ariaLabel={hasWhite ? `White LED of ${name}` : `Lighten ${name}`}
          />

          <LabeledSlider
            label={`Share — ${slot.weight}`}
            value={slot.weight}
            max={100}
            step={5}
            onChange={(value) => onChange({ ...slot, weight: value })}
            ariaLabel={`Weight of ${name}`}
          />
        </Box>
      </Collapse>
    </Box>
  )
}

function LabeledSlider({ label, value, max, step = 1, onChange, ariaLabel }) {
  return (
    <Box>
      {/* Matches the checkbox row beside the hue slider so the tracks line up. */}
      <Box sx={{ minHeight: { md: 38 }, display: 'flex', alignItems: 'center' }}>
        <Typography variant="caption">{label}</Typography>
      </Box>
      <Slider
        value={value}
        min={0}
        max={max}
        step={step}
        valueLabelDisplay="auto"
        onChange={(_event, next) => onChange(next)}
        aria-label={ariaLabel}
      />
    </Box>
  )
}
