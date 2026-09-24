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
import FormControlLabel from '@mui/material/FormControlLabel'
import InputLabel from '@mui/material/InputLabel'
import MenuItem from '@mui/material/MenuItem'
import Select from '@mui/material/Select'
import Slider from '@mui/material/Slider'
import Stack from '@mui/material/Stack'
import Switch from '@mui/material/Switch'
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
/** How often the strands are re-read while the page is open and visible. */
const LIVE_POLL_MS = 5000
/** For a pattern that doesn't say: a preset saved without one, or a new pattern. */
const DEFAULT_BRIGHTNESS = 60

const STARTING_PATTERN = {
  slots: [
    { rgbw: [255, 152, 0, 0], weight: 80 },
    { rgbw: [128, 0, 255, 0], weight: 20 },
  ],
  layout: 'interleaved',
  block_size: 1,
  brightness: DEFAULT_BRIGHTNESS,
}

const NEW_SLOT_COLORS = [
  [0, 160, 255, 0],
  [0, 255, 77, 0],
  [255, 0, 90, 0],
  [255, 200, 0, 0],
  [160, 0, 255, 0],
  [0, 0, 0, 255],
]

/**
 * "Showing “Halloween”, applied 18:04." — what the strands are doing, from
 * `live` (read from them) where there is one, else from what Dapple last sent.
 */
function stateSummary(group, live) {
  const state = group?.state
  const when =
    state &&
    new Date(state.applied_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const what = state?.preset ? `“${state.preset}”` : 'a custom pattern'
  const last = state ? ` Last applied ${what} at ${when}.` : ''

  if (!live) return state ? `Last applied ${what} at ${when}.` : 'Nothing applied yet.'
  if (live.power === null) return `No strands are answering.${last}`
  if (live.taken_over) {
    return (
      'Showing a color or effect set outside Dapple (the Twinkly app, or Home Assistant’s ' +
      'Twinkly integration). Apply a pattern to take it back.'
    )
  }
  if (live.power === 'off') {
    return state
      ? `Off. ${what[0].toUpperCase()}${what.slice(1)} comes back when switched on.`
      : 'Off.'
  }
  return state ? `Showing ${what}, applied ${when}.` : 'On.'
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
  // Indices of the colors whose controls are showing on a phone. Kept here
  // rather than in SlotRow because rows are keyed by index: moving a color must
  // carry its open state with it, not leave it behind at the old position.
  const [openSlots, setOpenSlots] = useState(() => new Set())
  // The preset the editor holds, untouched. Every edit builds a new pattern
  // object, so identity is enough to tell whether Apply is still applying that
  // preset, and the group's state can name it rather than "a custom pattern".
  const [fromPreset, setFromPreset] = useState(null)

  const group = groups.find((candidate) => candidate.id === groupId) || null

  // What the strands are doing, re-read every few seconds so a change made in
  // Home Assistant or the Twinkly app shows up here without a reload.
  const [liveReading, setLiveReading] = useState(null)
  const live = liveReading?.groupId === groupId ? liveReading : null
  const readLive = useCallback(async () => {
    if (!groupId) return
    try {
      setLiveReading({ groupId, ...(await api.getGroupLive(groupId)) })
    } catch {
      /* keep the last reading; the next poll tries again */
    }
  }, [groupId])

  useEffect(() => {
    readLive()
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') readLive()
    }, LIVE_POLL_MS)
    return () => clearInterval(timer)
  }, [readLive])

  // Follow changes made elsewhere — but only changes: taking the strand's
  // brightness on every poll would undo a preset loaded and not yet applied.
  const draggingBrightness = useRef(false)
  const lastLive = useRef(null)
  useEffect(() => {
    if (!live) return
    const previous = lastLive.current?.groupId === live.groupId ? lastLive.current : null
    lastLive.current = live
    if (!previous) return
    if (
      live.brightness !== null &&
      live.brightness !== previous.brightness &&
      !draggingBrightness.current
    ) {
      setPattern((current) => ({ ...current, brightness: live.brightness }))
    }
    // HA's commands go through Dapple, so "last applied" has moved too.
    if (live.power !== previous.power || live.preset !== previous.preset) onChanged?.()
  }, [live, onChanged])

  const known = Boolean(group && group.segments.length)
  // With nothing to go on, offer the white channel rather than hide a control
  // the strands may well support.
  const hasWhite = !known || group.segments.some((segment) => segment.led_profile === 'RGBW')
  // Until a strand answers we still want a preview, so fall back to a plausible
  // length rather than an empty bar.
  const previewLeds = group?.total_leds || FALLBACK_LEDS
  // Stable between renders, so the preview only redraws when something changed.
  const previewSegments = useMemo(
    () =>
      known
        ? group.segments
        : [{ name: 'preview', offset: 0, number_of_led: previewLeds, led_profile: 'RGB' }],
    [known, group?.segments, previewLeds],
  )

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
    // Not the preset it was applied from: that preset may have been edited since,
    // and applying by name would send its new colors, not the ones on screen.
    setFromPreset(null)
    setStatus(null)
    setOpenSlots(new Set())
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

  const remapOpen = (map) =>
    setOpenSlots((current) => new Set([...current].map(map).filter((i) => i !== null)))

  const toggleSlot = (index) =>
    setOpenSlots((current) => {
      const next = new Set(current)
      if (!next.delete(index)) next.add(index)
      return next
    })

  const removeSlot = (index) => {
    setPattern((current) => ({
      ...current,
      slots: current.slots.filter((_slot, i) => i !== index),
    }))
    remapOpen((i) => (i === index ? null : i > index ? i - 1 : i))
  }

  const moveSlot = (index, delta) => {
    const target = index + delta
    if (target < 0 || target >= pattern.slots.length) return
    setPattern((current) => {
      const slots = [...current.slots]
      ;[slots[index], slots[target]] = [slots[target], slots[index]]
      return { ...current, slots }
    })
    remapOpen((i) => (i === index ? target : i === target ? index : i))
  }

  // A color you just added is one you're about to pick, so it arrives open.
  const addSlot = () => {
    setPattern((current) => ({
      ...current,
      slots: [
        ...current.slots,
        { rgbw: NEW_SLOT_COLORS[current.slots.length % NEW_SLOT_COLORS.length], weight: 20 },
      ],
    }))
    setOpenSlots((current) => new Set(current).add(pattern.slots.length))
  }

  // ---- actions ------------------------------------------------------------

  const after = async (result) => {
    await Promise.all([onChanged?.(), readLive()])
    return result
  }

  const setPower = (on) => {
    setLiveReading((current) => current && { ...current, power: on ? 'on' : 'off' })
    return run(`${group.name} ${on ? 'on' : 'off'}`, () =>
      (on ? api.turnGroupOn(groupId) : api.turnGroupOff(groupId)).then(after),
    )
  }

  const onApply = () =>
    fromPreset?.pattern === pattern
      ? run(`Applied “${fromPreset.name}” to ${group.name}`, () =>
          api.applyPresetToGroup(groupId, fromPreset.name).then(after),
        )
      : run(`Applied to ${group.name}`, () => api.applyToGroup(groupId, pattern).then(after))

  const onLoadPreset = () => {
    const preset = presets[selected]
    if (!preset) return
    const next = { brightness: DEFAULT_BRIGHTNESS, ...preset }
    setPattern(next)
    setFromPreset({ name: selected, pattern: next })
    setOpenSlots(new Set())
    setStatus({ severity: 'info', text: `Loaded “${selected}” — not applied yet` })
  }

  // There's no undo, and a built-in preset is gone for good once deleted.
  const onDeletePreset = async () => {
    if (!window.confirm(`Delete the preset “${selected}”? This can't be undone.`)) return
    const deleted = await run(`Deleted “${selected}”`, () => api.deletePreset(selected))
    if (!deleted) return
    if (fromPreset?.name === selected) setFromPreset(null)
    setSelected('')
    loadPresets()
  }

  const onSavePreset = async () => {
    const name = saveName.trim()
    if (!name) return
    setSaveOpen(false)
    const saved = await run(`Saved “${name}”`, () => api.savePreset(name, pattern))
    if (!saved) return
    setFromPreset({ name, pattern })
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
                    <Tab key={candidate.id} value={candidate.id} label={candidate.name} />
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
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              spacing={1}
              sx={{ mb: 0.5 }}
            >
              <Typography variant="subtitle2" component="h2">
                {group?.name}
              </Typography>
              <FormControlLabel
                labelPlacement="start"
                label={
                  !live
                    ? 'Checking…'
                    : live.power === null
                      ? 'Not answering'
                      : live.power === 'on'
                        ? 'On'
                        : 'Off'
                }
                control={
                  <Switch
                    // Remounted by the first reading, so it appears in place
                    // rather than sliding across as the page opens.
                    key={live ? 'read' : 'unread'}
                    checked={live?.power === 'on'}
                    disabled={busy || !live || live.power === null}
                    onChange={(event) => setPower(event.target.checked)}
                    inputProps={{ 'aria-label': `${group?.name ?? 'Group'} power` }}
                  />
                }
                sx={{ mr: 0 }}
              />
            </Stack>
            <PreviewStrip
              pattern={pattern}
              totalLeds={previewLeds}
              segments={previewSegments}
              known={known}
            />
            <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
              {stateSummary(group, live)}
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
            <Typography variant="subtitle2" component="h2" color="text.secondary" gutterBottom>
              Colors
            </Typography>
            {pattern.slots.map((slot, index) => (
              <SlotRow
                key={index}
                slot={slot}
                index={index}
                share={totalWeight ? Math.round((slot.weight / totalWeight) * 100) : 0}
                hasWhite={hasWhite}
                open={openSlots.has(index)}
                onToggle={() => toggleSlot(index)}
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
              <Stack
                direction={{ xs: 'column', sm: 'row' }}
                spacing={2}
                alignItems={{ sm: 'center' }}
              >
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
                <Typography variant="caption">
                  Brightness — {pattern.brightness ?? DEFAULT_BRIGHTNESS}%
                </Typography>
                <Slider
                  value={pattern.brightness ?? DEFAULT_BRIGHTNESS}
                  min={0}
                  max={100}
                  valueLabelDisplay="auto"
                  onChange={(_event, value) => {
                    draggingBrightness.current = true
                    setPattern((current) => ({ ...current, brightness: value }))
                  }}
                  onChangeCommitted={(_event, value) => {
                    draggingBrightness.current = false
                    run(`Brightness ${value}%`, () =>
                      api.setGroupBrightness(groupId, value).then(after),
                    )
                  }}
                  aria-label="Brightness"
                />
              </Box>
            </Stack>
          </CardContent>
        </Card>

        <Card variant="outlined">
          <CardContent>
            <Stack spacing={2}>
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

              <Divider />

              <Stack
                direction={{ xs: 'column', sm: 'row' }}
                spacing={1.5}
                alignItems={{ sm: 'center' }}
              >
                <FormControl size="small" sx={{ minWidth: 200, flex: 1 }}>
                  <InputLabel id="preset-label">Preset</InputLabel>
                  <Select
                    labelId="preset-label"
                    label="Preset"
                    value={selected}
                    onChange={(event) => setSelected(event.target.value)}
                  >
                    {Object.keys(presets)
                      .sort()
                      .map((name) => (
                        <MenuItem key={name} value={name}>
                          {name}
                        </MenuItem>
                      ))}
                  </Select>
                </FormControl>
                <Button onClick={onLoadPreset} disabled={!selected}>
                  Load
                </Button>
                <Button color="error" onClick={onDeletePreset} disabled={!selected || busy}>
                  Delete
                </Button>
              </Stack>
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
