import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import Dialog from '@mui/material/Dialog'
import DialogActions from '@mui/material/DialogActions'
import DialogContent from '@mui/material/DialogContent'
import DialogTitle from '@mui/material/DialogTitle'
import Divider from '@mui/material/Divider'
import FormControl from '@mui/material/FormControl'
import InputLabel from '@mui/material/InputLabel'
import MenuItem from '@mui/material/MenuItem'
import Select from '@mui/material/Select'
import Slider from '@mui/material/Slider'
import Stack from '@mui/material/Stack'
import Tab from '@mui/material/Tab'
import Tabs from '@mui/material/Tabs'
import TextField from '@mui/material/TextField'
import ToggleButton from '@mui/material/ToggleButton'
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup'
import Typography from '@mui/material/Typography'
import AddIcon from '@mui/icons-material/Add'

import * as api from './api.js'
import PreviewStrip from './PreviewStrip.jsx'
import SlotRow from './SlotRow.jsx'

const MAX_SLOTS = 8
const FALLBACK_LEDS = 200
const TABS_UP_TO = 4

const STARTING_PATTERN = {
  slots: [
    { rgbw: [255, 0, 0, 0], weight: 1 },
    { rgbw: [0, 0, 0, 255], weight: 1 },
    { rgbw: [0, 0, 255, 0], weight: 1 },
  ],
  layout: 'interleaved',
  block_size: 1,
  brightness: 60,
}

const NEW_SLOT_COLORS = [
  [0, 160, 255, 0],
  [0, 200, 60, 0],
  [255, 0, 90, 0],
  [255, 200, 0, 0],
  [160, 0, 255, 0],
  [0, 0, 0, 255],
]

/** "Showing Halloween, applied 18:04" — what this group is currently set to. */
function stateSummary(group) {
  if (!group?.state) return 'Nothing applied yet.'
  const when = new Date(group.state.applied_at).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })
  const what = group.state.preset ? `“${group.state.preset}”` : 'a custom pattern'
  if (group.state.power === 'off') return `Off. Last applied ${what} at ${when}.`
  return `Last applied ${what} at ${when}.`
}

/** Build a pattern, preview it, and apply it to one group. */
export default function PatternPage({ groups, groupId, loaded, onSelectGroup, onChanged }) {
  const [pattern, setPattern] = useState(STARTING_PATTERN)
  const [presets, setPresets] = useState({})
  const [selected, setSelected] = useState('')
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const [saveOpen, setSaveOpen] = useState(false)
  const [saveName, setSaveName] = useState('')

  const group = groups.find((candidate) => candidate.id === groupId) || null
  const known = Boolean(group && group.segments.length)
  // With nothing to go on, offer the white channel rather than hide a control
  // the strands may well support.
  const hasWhite = !known || group.segments.some((segment) => segment.led_profile === 'RGBW')
  // Until a strand answers we still want a preview, so fall back to a plausible
  // length rather than an empty bar.
  const previewLeds = group?.total_leds || FALLBACK_LEDS
  const previewSegments = known
    ? group.segments
    : [{ name: 'preview', offset: 0, number_of_led: previewLeds, led_profile: 'RGB' }]

  const totalWeight = useMemo(
    () => pattern.slots.reduce((sum, slot) => sum + slot.weight, 0),
    [pattern.slots],
  )

  // Seed the editor from the group's own pattern, but only when the selection
  // actually changes — a background refresh must not stomp unsaved edits.
  const seededFor = useRef(null)
  useEffect(() => {
    if (!groupId || seededFor.current === groupId) return
    seededFor.current = groupId
    const current = groups.find((candidate) => candidate.id === groupId)
    setPattern(current?.state?.pattern ?? STARTING_PATTERN)
    setSelected(current?.state?.preset ?? '')
    setStatus(null)
  }, [groupId, groups])

  const run = useCallback(async (label, action) => {
    setBusy(true)
    try {
      const result = await action()
      if (result?.results) {
        const failed = result.results.filter((device) => !device.ok)
        setStatus(
          failed.length
            ? {
                severity: 'error',
                text: `${label} failed on ${failed
                  .map((device) => `${device.name}: ${device.error}`)
                  .join('; ')}`,
              }
            : {
                severity: 'success',
                text: `${label} — ${result.results.map((device) => device.name).join(', ')}`,
              },
        )
      } else {
        setStatus({ severity: 'success', text: label })
      }
      return result
    } catch (error) {
      setStatus({ severity: 'error', text: `${label} failed: ${error.message}` })
      return null
    } finally {
      setBusy(false)
    }
  }, [])

  const loadPresets = useCallback(async () => {
    try {
      setPresets(await api.getPresets())
    } catch {
      /* the status line already reports whatever the user just did */
    }
  }, [])

  useEffect(() => {
    loadPresets()
  }, [loadPresets])

  // ---- slot editing -------------------------------------------------------

  const updateSlot = (index, slot) =>
    setPattern((current) => ({
      ...current,
      slots: current.slots.map((existing, i) => (i === index ? slot : existing)),
    }))

  const removeSlot = (index) =>
    setPattern((current) => ({
      ...current,
      slots: current.slots.filter((_slot, i) => i !== index),
    }))

  const moveSlot = (index, delta) =>
    setPattern((current) => {
      const slots = [...current.slots]
      const target = index + delta
      if (target < 0 || target >= slots.length) return current
      ;[slots[index], slots[target]] = [slots[target], slots[index]]
      return { ...current, slots }
    })

  const addSlot = () =>
    setPattern((current) => ({
      ...current,
      slots: [
        ...current.slots,
        { rgbw: NEW_SLOT_COLORS[current.slots.length % NEW_SLOT_COLORS.length], weight: 20 },
      ],
    }))

  // ---- actions ------------------------------------------------------------

  const after = async (result) => {
    await onChanged?.()
    return result
  }

  const onApply = () =>
    run(`Applied to ${group.name}`, () => api.applyToGroup(groupId, pattern).then(after))

  const onLoadPreset = () => {
    const preset = presets[selected]
    if (!preset) return
    setPattern({ brightness: 60, ...preset })
    setStatus({ severity: 'info', text: `Loaded “${selected}” — not applied yet` })
  }

  const onApplyPreset = () =>
    run(`Applied “${selected}” to ${group.name}`, () =>
      api.applyPresetToGroup(groupId, selected).then(after),
    )

  const onDeletePreset = async () => {
    await run(`Deleted “${selected}”`, () => api.deletePreset(selected))
    setSelected('')
    loadPresets()
  }

  const onSavePreset = async () => {
    const name = saveName.trim()
    if (!name) return
    setSaveOpen(false)
    await run(`Saved “${name}”`, () => api.savePreset(name, pattern))
    setSelected(name)
    loadPresets()
  }

  if (loaded && !groups.length) {
    return (
      <Alert severity="info">
        No strands yet. Add one on the{' '}
        <Box component="a" href="#strands" sx={{ color: 'inherit' }}>
          Strands
        </Box>{' '}
        tab — it gets a group of its own, and a group is what holds a pattern.
      </Alert>
    )
  }

  return (
    <>
      <Stack spacing={2}>
        {groups.length > 1 && (
          <Card variant="outlined">
            <CardContent sx={{ pb: 1 }}>
              {groups.length <= TABS_UP_TO ? (
                <Tabs
                  value={groupId || false}
                  onChange={(_event, id) => onSelectGroup(id)}
                  variant="scrollable"
                  scrollButtons="auto"
                >
                  {groups.map((candidate) => (
                    <Tab
                      key={candidate.id}
                      value={candidate.id}
                      label={candidate.name}
                    />
                  ))}
                </Tabs>
              ) : (
                <FormControl size="small" fullWidth>
                  <InputLabel id="group-label">Group</InputLabel>
                  <Select
                    labelId="group-label"
                    label="Group"
                    value={groupId || ''}
                    onChange={(event) => onSelectGroup(event.target.value)}
                  >
                    {groups.map((candidate) => (
                      <MenuItem key={candidate.id} value={candidate.id}>
                        {candidate.name} — {candidate.total_leds} LEDs
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              )}
            </CardContent>
          </Card>
        )}

        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2" gutterBottom>
              {group?.name}
            </Typography>
            <PreviewStrip
              pattern={pattern}
              totalLeds={previewLeds}
              segments={previewSegments}
              known={known}
            />
            <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
              {stateSummary(group)}
            </Typography>
          </CardContent>
        </Card>

        {status && (
          <Alert severity={status.severity} onClose={() => setStatus(null)}>
            {status.text}
          </Alert>
        )}

        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2" color="text.secondary" gutterBottom>
              Colors
            </Typography>
            {pattern.slots.map((slot, index) => (
              <SlotRow
                key={index}
                slot={slot}
                index={index}
                share={totalWeight ? Math.round((slot.weight / totalWeight) * 100) : 0}
                hasWhite={hasWhite}
                canRemove={pattern.slots.length > 1}
                canMoveUp={index > 0}
                canMoveDown={index < pattern.slots.length - 1}
                onChange={(next) => updateSlot(index, next)}
                onRemove={() => removeSlot(index)}
                onMove={(delta) => moveSlot(index, delta)}
              />
            ))}
            <Button
              startIcon={<AddIcon />}
              onClick={addSlot}
              disabled={pattern.slots.length >= MAX_SLOTS}
              sx={{ mt: 1.5 }}
            >
              Add color
            </Button>
          </CardContent>
        </Card>

        <Card variant="outlined">
          <CardContent>
            <Stack spacing={2.5}>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ sm: 'center' }}>
                <ToggleButtonGroup
                  size="small"
                  exclusive
                  value={pattern.layout}
                  onChange={(_event, value) =>
                    value && setPattern((current) => ({ ...current, layout: value }))
                  }
                >
                  <ToggleButton value="interleaved">Interleaved</ToggleButton>
                  <ToggleButton value="blocked">Blocked</ToggleButton>
                </ToggleButtonGroup>

                {pattern.layout === 'blocked' && (
                  <TextField
                    size="small"
                    type="number"
                    label="LEDs per unit"
                    value={pattern.block_size}
                    onChange={(event) =>
                      setPattern((current) => ({
                        ...current,
                        block_size: Math.max(1, Number(event.target.value) || 1),
                      }))
                    }
                    inputProps={{ min: 1, max: 500 }}
                    sx={{ width: 150 }}
                  />
                )}
              </Stack>

              <Box>
                <Typography variant="caption" color="text.secondary">
                  Brightness — {pattern.brightness ?? 60}%
                </Typography>
                <Slider
                  value={pattern.brightness ?? 60}
                  min={0}
                  max={100}
                  valueLabelDisplay="auto"
                  onChange={(_event, value) =>
                    setPattern((current) => ({ ...current, brightness: value }))
                  }
                  onChangeCommitted={(_event, value) =>
                    run(`Brightness ${value}%`, () =>
                      api.setGroupBrightness(groupId, value).then(after),
                    )
                  }
                  aria-label="Brightness"
                />
              </Box>

              <Stack direction="row" spacing={1.5}>
                <Button
                  variant="outlined"
                  onClick={() => run(`${group.name} on`, () => api.turnGroupOn(groupId).then(after))}
                  disabled={busy}
                >
                  On
                </Button>
                <Button
                  variant="outlined"
                  onClick={() => run(`${group.name} off`, () => api.turnGroupOff(groupId).then(after))}
                  disabled={busy}
                >
                  Off
                </Button>
              </Stack>
            </Stack>
          </CardContent>
        </Card>

        <Card variant="outlined">
          <CardContent>
            <Stack spacing={2}>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ sm: 'center' }}>
                <FormControl size="small" sx={{ minWidth: 200, flex: 1 }}>
                  <InputLabel id="preset-label">Preset</InputLabel>
                  <Select
                    labelId="preset-label"
                    label="Preset"
                    value={selected}
                    onChange={(event) => setSelected(event.target.value)}
                  >
                    {Object.keys(presets).sort().map((name) => (
                      <MenuItem key={name} value={name}>
                        {name}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <Button onClick={onLoadPreset} disabled={!selected}>
                  Load
                </Button>
                <Button onClick={onApplyPreset} disabled={!selected || busy || !group}>
                  Apply preset
                </Button>
                <Button color="error" onClick={onDeletePreset} disabled={!selected || busy}>
                  Delete
                </Button>
              </Stack>

              <Divider />

              <Stack direction="row" spacing={1.5}>
                <Button
                  variant="contained"
                  size="large"
                  onClick={onApply}
                  disabled={busy || totalWeight === 0 || !group}
                  sx={{ flex: 1 }}
                >
                  {group ? `Apply to ${group.name}` : 'Apply'}
                </Button>
                <Button
                  variant="outlined"
                  size="large"
                  onClick={() => {
                    setSaveName(selected)
                    setSaveOpen(true)
                  }}
                >
                  Save as…
                </Button>
              </Stack>
              <Typography variant="caption" color="text.secondary">
                {totalWeight === 0
                  ? 'Every color is at 0% — give at least one of them a share.'
                  : 'Presets are shared across groups — apply the same one anywhere.'}
              </Typography>
            </Stack>
          </CardContent>
        </Card>
      </Stack>

      <Dialog open={saveOpen} onClose={() => setSaveOpen(false)} fullWidth maxWidth="xs">
        <DialogTitle>Save preset</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            margin="dense"
            label="Name"
            value={saveName}
            onChange={(event) => setSaveName(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && onSavePreset()}
            helperText="An existing name is replaced. Presets work on any group."
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSaveOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={onSavePreset} disabled={!saveName.trim()}>
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </>
  )
}
