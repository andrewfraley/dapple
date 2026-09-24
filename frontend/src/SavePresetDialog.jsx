import { useEffect, useState } from 'react'
import Button from '@mui/material/Button'
import Dialog from '@mui/material/Dialog'
import DialogActions from '@mui/material/DialogActions'
import DialogContent from '@mui/material/DialogContent'
import DialogTitle from '@mui/material/DialogTitle'
import TextField from '@mui/material/TextField'

/** Name the pattern in the editor. Starts from `initialName`, the preset it was loaded from. */
export default function SavePresetDialog({ open, initialName, onClose, onSave }) {
  const [name, setName] = useState('')

  useEffect(() => {
    if (open) setName(initialName)
  }, [open, initialName])

  const submit = () => name.trim() && onSave(name.trim())

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Save preset</DialogTitle>
      <DialogContent>
        <TextField
          autoFocus
          fullWidth
          margin="dense"
          label="Name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => event.key === 'Enter' && submit()}
          helperText="An existing name is replaced. Presets work on any group."
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={!name.trim()}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  )
}
