import { useEffect, useState } from 'react'
import Alert from '@mui/material/Alert'
import Button from '@mui/material/Button'
import Dialog from '@mui/material/Dialog'
import DialogActions from '@mui/material/DialogActions'
import DialogContent from '@mui/material/DialogContent'
import DialogTitle from '@mui/material/DialogTitle'
import FormControlLabel from '@mui/material/FormControlLabel'
import Stack from '@mui/material/Stack'
import Switch from '@mui/material/Switch'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'
import * as api from './api.js'

// The server's clock may be on UTC, so the zone comes from whoever sets the schedule.
const browserZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || ''

const hasTimer = (strand) =>
  strand.reachable && ((strand.time_on ?? -1) !== -1 || (strand.time_off ?? -1) !== -1)

/** One group's daily on and off times. They run on the strands, so Dapple can be off. */
export default function ScheduleDialog({ open, group, busy, error, onClose, onSave }) {
  const [enabled, setEnabled] = useState(true)
  const [timeOn, setTimeOn] = useState('')
  const [timeOff, setTimeOff] = useState('')
  const [timezone, setTimezone] = useState('')
  const [saved, setSaved] = useState(null)
  const [strands, setStrands] = useState([])
  const [loadError, setLoadError] = useState(null)

  useEffect(() => {
    if (!open || !group) return undefined
    let current = true
    setLoadError(null)
    setStrands([])
    setSaved(null)
    setEnabled(true)
    setTimeOn('')
    setTimeOff('')
    setTimezone(browserZone())
    api
      .getGroupSchedule(group.id)
      .then((result) => {
        if (!current) return
        setStrands(result.strands)
        setSaved(result.schedule)
        if (result.schedule) {
          setEnabled(result.schedule.enabled)
          setTimeOn(result.schedule.time_on || '')
          setTimeOff(result.schedule.time_off || '')
          setTimezone(result.schedule.timezone)
        }
      })
      .catch((reason) => current && setLoadError(reason.message))
    return () => {
      current = false
    }
  }, [open, group])

  const silent = strands.filter((strand) => !strand.reachable).map((strand) => strand.name)
  // With no schedule saved, a timer on a strand was set somewhere else, like the Twinkly app.
  const replaced = saved ? [] : strands.filter(hasTimer).map((strand) => strand.name)

  const submit = () =>
    onSave({
      enabled,
      time_on: timeOn || null,
      time_off: timeOff || null,
      timezone: timezone.trim(),
    })

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Schedule for {group?.name}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          {loadError && <Alert severity="error">{loadError}</Alert>}
          {replaced.length > 0 && (
            <Alert severity="warning">
              {replaced.join(', ')} already {replaced.length === 1 ? 'has' : 'have'} a timer, set
              outside Dapple. Saving replaces it.
            </Alert>
          )}
          {silent.length > 0 && (
            <Alert severity="info">
              {silent.join(', ')} didn't answer, and will pick this up when it's back.
            </Alert>
          )}
          <FormControlLabel
            control={
              <Switch checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
            }
            label="Turn on and off by the clock"
          />
          <Stack direction="row" spacing={2}>
            <TextField
              fullWidth
              type="time"
              label="On at"
              value={timeOn}
              disabled={!enabled}
              onChange={(event) => setTimeOn(event.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
            />
            <TextField
              fullWidth
              type="time"
              label="Off at"
              value={timeOff}
              disabled={!enabled}
              onChange={(event) => setTimeOff(event.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
            />
          </Stack>
          <TextField
            fullWidth
            label="Time zone"
            placeholder="America/Chicago"
            value={timezone}
            onChange={(event) => setTimezone(event.target.value)}
          />
          <Typography variant="caption" color="text.secondary">
            These times repeat every day, and the lights follow them even with Dapple switched off.
            They come on showing whatever the group last showed. Applying a pattern while they're
            off lights them until the next off time, and switching them off by hand doesn't cancel
            the next on time. Leave a time blank to skip it.
          </Typography>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={submit} disabled={busy} variant="contained">
          Save
        </Button>
      </DialogActions>
    </Dialog>
  )
}
