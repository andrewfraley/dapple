import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import Chip from '@mui/material/Chip'
import IconButton from '@mui/material/IconButton'
import MenuItem from '@mui/material/MenuItem'
import Select from '@mui/material/Select'
import Stack from '@mui/material/Stack'
import Table from '@mui/material/Table'
import TableBody from '@mui/material/TableBody'
import TableCell from '@mui/material/TableCell'
import TableHead from '@mui/material/TableHead'
import TableRow from '@mui/material/TableRow'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import AddIcon from '@mui/icons-material/Add'
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward'
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import EditIcon from '@mui/icons-material/Edit'

/** The LED count in play. "pinned" means config.yaml overrides the strand. */
function LedCell({ strand, info }) {
  if (strand.number_of_led) {
    return (
      <Stack direction="row" spacing={1} alignItems="center" justifyContent="flex-end">
        <span>{strand.number_of_led}</span>
        <Chip label="pinned" size="small" variant="outlined" />
      </Stack>
    )
  }
  if (info?.number_of_led) return <span>{info.number_of_led}</span>
  return <Typography color="text.secondary">—</Typography>
}

function ProfileCell({ strand, info }) {
  const profile = strand.led_profile || info?.led_profile
  if (!profile) return <Typography color="text.secondary">—</Typography>
  return (
    <Stack direction="row" spacing={1} alignItems="center">
      <span>{profile}</span>
      {strand.led_profile && <Chip label="pinned" size="small" variant="outlined" />}
    </Stack>
  )
}

function StatusCell({ info }) {
  if (!info) return <Chip label="not checked" size="small" />
  if (info.reachable) {
    return <Chip label={`ok · fw ${info.fw_version || '?'}`} size="small" color="success" />
  }
  return (
    <Tooltip title={info.error || 'No answer'}>
      <Chip label="no answer" size="small" color="error" />
    </Tooltip>
  )
}

/**
 * One group: its strands in physical order, and the controls that change them.
 *
 * Order here is the seam — strand 2 picks up where strand 1 stopped. Order
 * between groups is display only.
 */
export default function GroupSection({
  group,
  groups,
  status,
  busy,
  canMoveUp,
  canMoveDown,
  infoFor,
  onAddStrand,
  onEditStrand,
  onDeleteStrand,
  onMoveStrand,
  onReorderStrand,
  onRename,
  onDelete,
  onMoveGroup,
}) {
  const strands = group.strands
  const others = groups.filter((candidate) => candidate.id !== group.id)
  const totalLeds = status?.total_leds ?? 0

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack direction="row" alignItems="flex-start" spacing={1} sx={{ mb: 1 }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="subtitle1">{group.name}</Typography>
            <Typography variant="caption" color="text.secondary">
              {strands.length
                ? `${strands.length} strand${strands.length === 1 ? '' : 's'} · ${totalLeds} LEDs · one pattern, starting at this group's first LED`
                : 'No strands yet — this group has nothing to light.'}
            </Typography>
          </Box>
          <Stack direction="row" spacing={0.5}>
            <Tooltip title="Move group up">
              <span>
                <IconButton size="small" disabled={busy || !canMoveUp} onClick={() => onMoveGroup(-1)}>
                  <ArrowUpwardIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Move group down">
              <span>
                <IconButton size="small" disabled={busy || !canMoveDown} onClick={() => onMoveGroup(1)}>
                  <ArrowDownwardIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Rename group">
              <span>
                <IconButton size="small" disabled={busy} onClick={onRename}>
                  <EditIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip
              title={
                strands.length
                  ? `Move or remove its ${strands.length} strand${strands.length === 1 ? '' : 's'} first`
                  : 'Delete group'
              }
            >
              <span>
                <IconButton size="small" disabled={busy || strands.length > 0} onClick={onDelete}>
                  <DeleteOutlineIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
        </Stack>

        {strands.length > 0 && (
          <Box sx={{ overflowX: 'auto' }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Name</TableCell>
                  <TableCell>Address</TableCell>
                  <TableCell align="right">LEDs</TableCell>
                  <TableCell>Channels</TableCell>
                  <TableCell>Status</TableCell>
                  {others.length > 0 && <TableCell>Group</TableCell>}
                  <TableCell align="right">Order</TableCell>
                  <TableCell align="right" />
                </TableRow>
              </TableHead>
              <TableBody>
                {strands.map((strand, index) => (
                  <TableRow key={strand.host} hover>
                    <TableCell>{strand.name}</TableCell>
                    <TableCell>
                      <Box component="code" sx={{ fontSize: '0.85em' }}>
                        {strand.host}
                      </Box>
                    </TableCell>
                    <TableCell align="right">
                      <LedCell strand={strand} info={infoFor(strand.host)} />
                    </TableCell>
                    <TableCell>
                      <ProfileCell strand={strand} info={infoFor(strand.host)} />
                    </TableCell>
                    <TableCell>
                      <StatusCell info={infoFor(strand.host)} />
                    </TableCell>
                    {others.length > 0 && (
                      <TableCell>
                        <Select
                          size="small"
                          value={group.id}
                          disabled={busy}
                          onChange={(event) => onMoveStrand(strand.host, event.target.value)}
                          sx={{ minWidth: 120 }}
                          aria-label={`Group of ${strand.name}`}
                        >
                          <MenuItem value={group.id}>{group.name}</MenuItem>
                          {others.map((candidate) => (
                            <MenuItem key={candidate.id} value={candidate.id}>
                              {candidate.name}
                            </MenuItem>
                          ))}
                        </Select>
                      </TableCell>
                    )}
                    <TableCell align="right">
                      <IconButton
                        size="small"
                        disabled={busy || index === 0}
                        onClick={() => onReorderStrand(index, -1)}
                        aria-label={`Move ${strand.name} earlier`}
                      >
                        <ArrowUpwardIcon fontSize="small" />
                      </IconButton>
                      <IconButton
                        size="small"
                        disabled={busy || index === strands.length - 1}
                        onClick={() => onReorderStrand(index, 1)}
                        aria-label={`Move ${strand.name} later`}
                      >
                        <ArrowDownwardIcon fontSize="small" />
                      </IconButton>
                    </TableCell>
                    <TableCell align="right">
                      <IconButton
                        size="small"
                        disabled={busy}
                        onClick={() => onEditStrand(strand)}
                        aria-label={`Edit ${strand.name}`}
                      >
                        <EditIcon fontSize="small" />
                      </IconButton>
                      <IconButton
                        size="small"
                        disabled={busy}
                        onClick={() => onDeleteStrand(strand)}
                        aria-label={`Remove ${strand.name}`}
                      >
                        <DeleteOutlineIcon fontSize="small" />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}

        <Button startIcon={<AddIcon />} onClick={onAddStrand} disabled={busy} sx={{ mt: 1.5 }}>
          Add strand to {group.name}
        </Button>
      </CardContent>
    </Card>
  )
}
