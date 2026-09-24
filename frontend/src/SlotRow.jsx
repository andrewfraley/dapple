import Box from '@mui/material/Box'
import IconButton from '@mui/material/IconButton'
import Slider from '@mui/material/Slider'
import Stack from '@mui/material/Stack'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward'
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward'

import { cssColor, hexToRgb, rgbToHex } from './pattern.js'

/**
 * One color in the pattern: the color itself, its white channel on RGBW
 * strands, and its share of the LEDs.
 */
export default function SlotRow({
  slot,
  index,
  share,
  hasWhite,
  canRemove,
  canMoveUp,
  canMoveDown,
  onChange,
  onRemove,
  onMove,
}) {
  const [r, g, b, w] = slot.rgbw
  const setRgbw = (rgbw) => onChange({ ...slot, rgbw })

  return (
    <Stack
      direction={{ xs: 'column', sm: 'row' }}
      spacing={2}
      alignItems={{ xs: 'stretch', sm: 'center' }}
      sx={{ py: 1.5, borderBottom: 1, borderColor: 'divider' }}
    >
      <Stack direction="row" spacing={1.5} alignItems="center">
        <Box
          component="input"
          type="color"
          aria-label={`Color ${index + 1}`}
          value={rgbToHex([r, g, b])}
          onChange={(event) => setRgbw([...hexToRgb(event.target.value), w])}
          sx={{
            width: 48,
            height: 48,
            p: 0,
            border: 1,
            borderColor: 'grey.600',
            borderRadius: 1.5,
            background: 'none',
            cursor: 'pointer',
            flexShrink: 0,
            overflow: 'hidden',
            // Browsers pad the swatch and give it a border of its own, which
            // shows up as a second outline inside ours.
            '&::-webkit-color-swatch-wrapper': { p: 0 },
            '&::-webkit-color-swatch': { border: 'none' },
            '&::-moz-color-swatch': { border: 'none' },
          }}
        />
        <Box
          sx={{
            width: 28,
            height: 28,
            borderRadius: '50%',
            border: 1,
            borderColor: 'grey.600',
            backgroundColor: cssColor(slot.rgbw),
            flexShrink: 0,
          }}
          title="How this slot will look with its white channel mixed in"
        />
      </Stack>

      <Box sx={{ flex: 1, minWidth: 140 }}>
        <Typography variant="caption" color="text.secondary">
          Share — {share}% ({slot.weight})
        </Typography>
        <Slider
          size="small"
          value={slot.weight}
          min={0}
          max={100}
          step={5}
          valueLabelDisplay="auto"
          onChange={(_event, value) => onChange({ ...slot, weight: value })}
          aria-label={`Weight of color ${index + 1}`}
        />
      </Box>

      {hasWhite && (
        <Box sx={{ flex: 1, minWidth: 140 }}>
          <Typography variant="caption" color="text.secondary">
            White — {w}
          </Typography>
          <Slider
            size="small"
            value={w}
            min={0}
            max={255}
            valueLabelDisplay="auto"
            onChange={(_event, value) => setRgbw([r, g, b, value])}
            aria-label={`White channel of color ${index + 1}`}
          />
        </Box>
      )}

      <Stack direction="row" spacing={0.5} justifyContent="flex-end">
        <Tooltip describeChild title="Move up">
          <span>
            <IconButton
              size="small"
              disabled={!canMoveUp}
              onClick={() => onMove(-1)}
              aria-label={`Move color ${index + 1} up`}
            >
              <ArrowUpwardIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip describeChild title="Move down">
          <span>
            <IconButton
              size="small"
              disabled={!canMoveDown}
              onClick={() => onMove(1)}
              aria-label={`Move color ${index + 1} down`}
            >
              <ArrowDownwardIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip describeChild title="Remove color">
          <span>
            <IconButton
              size="small"
              disabled={!canRemove}
              onClick={onRemove}
              aria-label={`Remove color ${index + 1}`}
            >
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Stack>
    </Stack>
  )
}
