import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import FormControl from '@mui/material/FormControl'
import InputLabel from '@mui/material/InputLabel'
import MenuItem from '@mui/material/MenuItem'
import Select from '@mui/material/Select'
import Tab from '@mui/material/Tab'
import Tabs from '@mui/material/Tabs'

/** Up to this many groups fit as tabs on a phone; more become a dropdown. */
const TABS_UP_TO = 4

/** Which group the Pattern tab is editing. Hidden when there's only one. */
export default function GroupPicker({ groups, groupId, onSelect }) {
  if (groups.length <= 1) return null
  return (
    <Card variant="outlined">
      <CardContent sx={{ pb: 1 }}>
        {groups.length <= TABS_UP_TO ? (
          <Tabs
            value={groupId || false}
            onChange={(_event, id) => onSelect(id)}
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
              onChange={(event) => onSelect(event.target.value)}
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
  )
}
