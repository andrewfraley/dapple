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

// Mirrors config.RESERVED_GROUP_IDS.
const RESERVED = new Set(['order', 'all', 'new'])

/**
 * The id the server will give a new group — mirrors config.slugify and
 * config.unique_group_id. The dialog calls it permanent, so it has to be the
 * real one, suffix and all.
 */
function previewId(name, takenIds) {
  let base = (name || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32)
    .replace(/^-+|-+$/g, '')
  base = base || 'group'
  if (RESERVED.has(base)) base = `${base}-group`
  let candidate = base
  for (let suffix = 2; takenIds.includes(candidate); suffix += 1) candidate = `${base}-${suffix}`
  return candidate
}

/** Create or rename a group. `group` null means create. */
export default function GroupDialog({ open, group, takenIds, busy, error, onClose, onSave }) {
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
                Its id stays <code>{group.id}</code>, so anything already pointing at this group
                keeps working.
              </>
            ) : (
              <>
                Addressed as <code>{previewId(name, takenIds)}</code> by the API and Home Assistant.
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
