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

/** The id the server will derive from a name — mirrors config.slugify. */
function previewId(name) {
  const slug = (name || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32)
    .replace(/^-+|-+$/g, '')
  return slug || 'group'
}

/** Create or rename a group. `group` null means create. */
export default function GroupDialog({ open, group, busy, error, onClose, onSave }) {
  const [name, setName] = useState('')

  useEffect(() => {
    if (open) setName(group?.name || '')
  }, [open, group])

  const submit = () => name.trim() && onSave(name.trim())

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>{group ? 'Rename group' : 'New group'}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            autoFocus
            fullWidth
            label="Name"
            placeholder="Christmas tree"
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && submit()}
          />
          <Typography variant="caption" color="text.secondary">
            {group ? (
              <>
                Its id stays <code>{group.id}</code>, so anything already pointing at this
                group keeps working.
              </>
            ) : (
              <>
                Addressed as <code>{previewId(name)}</code> by the API and Home Assistant.
                That id is permanent; the name isn’t.
              </>
            )}
          </Typography>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={busy || !name.trim()}>
          {group ? 'Save' : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
