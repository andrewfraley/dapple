import { useEffect, useState } from 'react'
import Alert from '@mui/material/Alert'
import Button from '@mui/material/Button'
import Dialog from '@mui/material/Dialog'
import DialogActions from '@mui/material/DialogActions'
import DialogContent from '@mui/material/DialogContent'
import DialogTitle from '@mui/material/DialogTitle'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'

const BLANK = { name: '', host: '' }

/**
 * Add or edit one strand. `strand` null means add.
 *
 * The address is all there is to set: the strand reports its own LED count and
 * color channels, and those are the numbers Dapple has to use — a frame built
 * for a different LED count is one the strand won't accept.
 */
export default function StrandDialog({
  open,
  strand,
  info,
  groupName,
  busy,
  error,
  onClose,
  onSave,
}) {
  const [form, setForm] = useState(BLANK)

  useEffect(() => {
    if (!open) return
    setForm({ name: strand?.name || '', host: strand?.host || '' })
  }, [open, strand])

  const set = (field) => (event) => setForm({ ...form, [field]: event.target.value })

  const submit = () => {
    if (!form.host.trim()) return
    // Anything pinned in config.yaml by hand is passed through untouched.
    onSave({
      name: form.name.trim(),
      host: form.host.trim(),
      number_of_led: strand?.number_of_led ?? null,
      led_profile: strand?.led_profile ?? null,
    })
  }

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>{strand ? 'Edit strand' : 'Add strand'}</DialogTitle>
      <DialogContent>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}

          <TextField
            autoFocus={!strand}
            fullWidth
            label="Hostname or IP address"
            placeholder="192.168.40.21"
            value={form.host}
            onChange={set('host')}
            onKeyDown={(event) => event.key === 'Enter' && submit()}
            helperText="Just the address — no http:// and no port."
          />

          <TextField
            fullWidth
            label="Name"
            placeholder="Front porch"
            value={form.name}
            onChange={set('name')}
            onKeyDown={(event) => event.key === 'Enter' && submit()}
            helperText="Optional — what the preview and status lines call this strand."
          />

          <Typography variant="caption" color="text.secondary">
            {info?.reachable
              ? `This strand reports ${info.number_of_led} LEDs and ${info.led_profile}.`
              : 'LED count and color channels are read from the strand when it answers.'}
            {!strand &&
              (groupName
                ? ` Joining ${groupName}.`
                : ' It gets a group of its own; you can move it in with others later.')}
          </Typography>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={busy || !form.host.trim()}>
          {strand ? 'Save' : 'Add strand'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
