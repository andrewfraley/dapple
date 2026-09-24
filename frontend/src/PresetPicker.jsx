import Button from '@mui/material/Button'
import FormControl from '@mui/material/FormControl'
import InputLabel from '@mui/material/InputLabel'
import MenuItem from '@mui/material/MenuItem'
import Select from '@mui/material/Select'
import Stack from '@mui/material/Stack'

/** Choose a saved preset, then load it into the editor or delete it. */
export default function PresetPicker({ names, selected, busy, onSelect, onLoad, onDelete }) {
  return (
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ sm: 'center' }}>
      <FormControl size="small" sx={{ minWidth: 200, flex: 1 }}>
        <InputLabel id="preset-label">Preset</InputLabel>
        <Select
          labelId="preset-label"
          label="Preset"
          value={selected}
          onChange={(event) => onSelect(event.target.value)}
        >
          {[...names].sort().map((name) => (
            <MenuItem key={name} value={name}>
              {name}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
      <Button onClick={onLoad} disabled={!selected}>
        Load
      </Button>
      <Button color="error" onClick={onDelete} disabled={!selected || busy}>
        Delete
      </Button>
    </Stack>
  )
}
