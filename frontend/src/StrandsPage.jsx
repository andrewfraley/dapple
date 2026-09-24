import { useCallback, useEffect, useState } from 'react'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import AddIcon from '@mui/icons-material/Add'

import * as api from './api.js'
import GroupDialog from './GroupDialog.jsx'
import GroupSection from './GroupSection.jsx'
import StrandDialog from './StrandDialog.jsx'

/**
 * Groups and the strands in them. Everything here writes config.yaml, so the
 * arrangement survives a restart.
 */
export default function StrandsPage({ groups: status, onChanged }) {
  const [config, setConfig] = useState({
    groups: [],
    writable: true,
    state_writable: true,
    source: '',
  })
  const [live, setLive] = useState([])
  const [message, setMessage] = useState(null)
  const [busy, setBusy] = useState(false)
  const [editingStrand, setEditingStrand] = useState(null) // null closed, {group} adding
  const [editingGroup, setEditingGroup] = useState(null) // null closed, {} creating
  const [dialogError, setDialogError] = useState(null)

  const infoFor = (host) => live.find((device) => device.host === host)
  const statusFor = (id) => status.find((group) => group.id === id)

  const load = useCallback(async () => {
    try {
      const [nextConfig, devices] = await Promise.all([api.getConfig(), api.getDevices()])
      setConfig(nextConfig)
      setLive(devices)
    } catch (error) {
      setMessage({ severity: 'error', text: `Could not read the strand list: ${error.message}` })
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const run = async (label, action) => {
    setBusy(true)
    try {
      const result = await action()
      setMessage({ severity: 'success', text: label })
      await load()
      await onChanged?.()
      return result
    } catch (error) {
      setMessage({ severity: 'error', text: `${label} failed: ${error.message}` })
      return null
    } finally {
      setBusy(false)
    }
  }

  // ---- strands ------------------------------------------------------------

  const onSaveStrand = async (strand) => {
    setBusy(true)
    setDialogError(null)
    try {
      const saved = editingStrand?.host
        ? await api.updateStrand(editingStrand.host, strand)
        : await api.addStrand({ ...strand, group: editingStrand?.group ?? null })
      setEditingStrand(null)
      await load()
      await onChanged?.()
      setMessage(
        saved.info?.reachable
          ? {
              severity: 'success',
              text: `${saved.config.name} answered: ${saved.info.number_of_led} LEDs, ${saved.info.led_profile}, firmware ${saved.info.fw_version}`,
            }
          : {
              severity: 'warning',
              text: `${saved.config.name} saved, but it didn't answer: ${saved.info?.error || 'no response'}`,
            },
      )
    } catch (error) {
      setDialogError(error.message)
    } finally {
      setBusy(false)
    }
  }

  const reorderStrand = (group, index, delta) => {
    const hosts = group.strands.map((strand) => strand.host)
    const target = index + delta
    if (target < 0 || target >= hosts.length) return
    ;[hosts[index], hosts[target]] = [hosts[target], hosts[index]]
    run(`Reordered ${group.name}`, () => api.reorderGroupStrands(group.id, hosts))
  }

  const moveStrand = (host, groupId) => {
    const target = config.groups.find((group) => group.id === groupId)
    run(`Moved to ${target?.name ?? groupId}`, () => api.moveStrand(host, groupId))
  }

  const removeStrand = (strand) => {
    if (!window.confirm(`Remove ${strand.name} (${strand.host})?`)) return
    run(`Removed ${strand.name}`, () => api.deleteStrand(strand.host))
  }

  // ---- groups -------------------------------------------------------------

  const onSaveGroup = async (name) => {
    setBusy(true)
    setDialogError(null)
    try {
      if (editingGroup?.id) {
        await api.renameGroup(editingGroup.id, name)
      } else {
        await api.createGroup(name)
      }
      setEditingGroup(null)
      await load()
      await onChanged?.()
      setMessage({ severity: 'success', text: `Saved “${name}”` })
    } catch (error) {
      setDialogError(error.message)
    } finally {
      setBusy(false)
    }
  }

  const removeGroup = (group) => run(`Deleted “${group.name}”`, () => api.deleteGroup(group.id))

  const moveGroup = (index, delta) => {
    const ids = config.groups.map((group) => group.id)
    const target = index + delta
    if (target < 0 || target >= ids.length) return
    ;[ids[index], ids[target]] = [ids[target], ids[index]]
    run('Reordered groups', () => api.reorderGroups(ids))
  }

  return (
    <Stack spacing={2}>
      {!config.writable && (
        <Alert severity="warning">
          The config file can't be written, so changes here won't survive a restart. See
          “Permissions on ./data” in the README.
        </Alert>
      )}
      {config.writable && !config.state_writable && (
        <Alert severity="warning">
          Each group's last-applied pattern can't be saved, so the editor won't remember what a
          group is showing after a restart. Applying still works.
        </Alert>
      )}

      {message && (
        <Alert severity={message.severity} onClose={() => setMessage(null)}>
          {message.text}
        </Alert>
      )}

      <Stack direction="row" alignItems="center" spacing={1}>
        <Box sx={{ flex: 1 }}>
          <Typography variant="caption" color="text.secondary">
            A group gets one pattern. Strand order <em>within</em> a group is physical order —
            that's what makes a pattern continue across the join. Group order is display only.
          </Typography>
        </Box>
        <Button startIcon={<AddIcon />} onClick={() => setEditingGroup({})} disabled={busy}>
          New group
        </Button>
      </Stack>

      {config.groups.length === 0 ? (
        <Alert severity="info">
          No strands yet. Add one by hostname or IP address — it gets a group of its own, and you
          can move it in with others later.
          <Box sx={{ mt: 1.5 }}>
            <Button
              variant="contained"
              size="small"
              onClick={() => setEditingStrand({ group: null })}
            >
              Add strand
            </Button>
          </Box>
        </Alert>
      ) : (
        config.groups.map((group, index) => (
          <GroupSection
            key={group.id}
            group={group}
            groups={config.groups}
            status={statusFor(group.id)}
            busy={busy}
            canMoveUp={index > 0}
            canMoveDown={index < config.groups.length - 1}
            infoFor={infoFor}
            onAddStrand={() => setEditingStrand({ group: group.id })}
            onEditStrand={(strand) => setEditingStrand(strand)}
            onDeleteStrand={removeStrand}
            onMoveStrand={moveStrand}
            onReorderStrand={(position, delta) => reorderStrand(group, position, delta)}
            onRename={() => setEditingGroup(group)}
            onDelete={() => removeGroup(group)}
            onMoveGroup={(delta) => moveGroup(index, delta)}
          />
        ))
      )}

      {config.groups.length > 0 && (
        <Stack direction="row" spacing={1.5}>
          <Button
            size="small"
            disabled={busy || config.groups.every((group) => !group.strands.length)}
            onClick={() => run('Re-checked every strand', api.refreshDevices)}
          >
            Re-check all
          </Button>
        </Stack>
      )}

      {config.source && config.source !== 'none' && (
        <Typography variant="caption" color="text.secondary">
          {config.source === 'TWINKLY_HOSTS'
            ? 'Loaded from the TWINKLY_HOSTS environment variable — the first change here writes a config file that takes over from it.'
            : `Saved to ${config.source}`}
        </Typography>
      )}

      <StrandDialog
        open={editingStrand !== null}
        strand={editingStrand?.host ? editingStrand : null}
        info={editingStrand?.host ? infoFor(editingStrand.host) : null}
        groupName={
          editingStrand?.group
            ? config.groups.find((group) => group.id === editingStrand.group)?.name
            : null
        }
        busy={busy}
        error={dialogError}
        onClose={() => {
          setEditingStrand(null)
          setDialogError(null)
        }}
        onSave={onSaveStrand}
      />

      <GroupDialog
        open={editingGroup !== null}
        group={editingGroup?.id ? editingGroup : null}
        busy={busy}
        error={dialogError}
        onClose={() => {
          setEditingGroup(null)
          setDialogError(null)
        }}
        onSave={onSaveGroup}
      />
    </Stack>
  )
}
