import { useCallback, useEffect, useState } from 'react'
import Alert from '@mui/material/Alert'
import Button from '@mui/material/Button'
import FormControlLabel from '@mui/material/FormControlLabel'
import Link from '@mui/material/Link'
import Paper from '@mui/material/Paper'
import Stack from '@mui/material/Stack'
import Switch from '@mui/material/Switch'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'

import * as api from './api.js'
import { REPO_URL } from './links.js'

const DOCS_URL = `${REPO_URL}/blob/main/HOME_ASSISTANT.md`

/** How often the connection status is re-read while this tab is open. */
const STATUS_POLL_MS = 3000

// Off until someone switches it on: filling in an address shouldn't be enough
// to start publishing to a broker.
const EMPTY_FORM = {
  enabled: false,
  host: '',
  port: '1883',
  username: '',
  password: '',
  discovery_prefix: 'homeassistant',
  topic_prefix: 'dapple',
}

function formFrom(settings) {
  return {
    enabled: settings.enabled,
    host: settings.host || '',
    port: String(settings.port),
    username: settings.username || '',
    password: '',
    discovery_prefix: settings.discovery_prefix,
    topic_prefix: settings.topic_prefix,
  }
}

function StatusLine({ settings }) {
  if (!settings) return null
  switch (settings.status) {
    case 'connected':
      return (
        <Alert severity="success">
          Connected. Your groups are in Home Assistant under Settings → Devices &amp; services →
          MQTT.
        </Alert>
      )
    case 'connecting':
      return <Alert severity="info">Connecting to {settings.host}…</Alert>
    case 'error':
      return <Alert severity="error">{settings.error}</Alert>
    default:
      return settings.host ? <Alert severity="info">Switched off.</Alert> : null
  }
}

/**
 * Broker settings for MQTT discovery. The password is write-only: the server
 * only says whether one is saved, and leaving the field blank keeps it.
 */
export default function HomeAssistantPage() {
  const [settings, setSettings] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [advanced, setAdvanced] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)

  // Polling refreshes the status only; it never overwrites what's being typed.
  const readStatus = useCallback(async () => {
    try {
      setSettings(await api.getMqtt())
    } catch {
      /* the next poll will try again */
    }
  }, [])

  useEffect(() => {
    api
      .getMqtt()
      .then((loaded) => {
        setSettings(loaded)
        setForm(formFrom(loaded))
        setAdvanced(
          loaded.discovery_prefix !== EMPTY_FORM.discovery_prefix ||
            loaded.topic_prefix !== EMPTY_FORM.topic_prefix,
        )
      })
      .catch((error) =>
        setMessage({ severity: 'error', text: `Could not read the settings: ${error.message}` }),
      )
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') readStatus()
    }, STATUS_POLL_MS)
    return () => clearInterval(timer)
  }, [readStatus])

  const field = (name) => ({
    value: form[name],
    onChange: (event) => setForm({ ...form, [name]: event.target.value }),
  })

  const save = async (overrides = {}) => {
    setBusy(true)
    setMessage(null)
    const payload = {
      enabled: form.enabled,
      host: form.host.trim() || null,
      port: Number(form.port) || 1883,
      username: form.username.trim() || null,
      discovery_prefix: form.discovery_prefix.trim(),
      topic_prefix: form.topic_prefix.trim(),
    }
    if (form.password) payload.password = form.password
    Object.assign(payload, overrides)
    try {
      const saved = await api.saveMqtt(payload)
      setSettings(saved)
      setForm(formFrom(saved))
    } catch (error) {
      setMessage({ severity: 'error', text: error.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Stack spacing={2}>
      <Typography variant="body1">
        Each group shows up in Home Assistant as a light: on and off, brightness, and your presets
        as its effects. Dapple reaches Home Assistant through an MQTT broker. If you don’t have one
        yet, install the Mosquitto add-on and the MQTT integration in Home Assistant first.{' '}
        <Link href={DOCS_URL} target="_blank" rel="noreferrer">
          Step-by-step guide
        </Link>
      </Typography>

      <StatusLine settings={settings} />
      {message && (
        <Alert severity={message.severity} onClose={() => setMessage(null)}>
          {message.text}
        </Alert>
      )}

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack spacing={2}>
          <FormControlLabel
            control={
              <Switch
                checked={form.enabled}
                onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
              />
            }
            label="Connect to Home Assistant"
          />
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
            <TextField
              fullWidth
              label="Broker address"
              placeholder="192.168.1.20"
              helperText="With the Mosquitto add-on, this is your Home Assistant’s address"
              {...field('host')}
            />
            <TextField label="Port" type="number" sx={{ width: { sm: 140 } }} {...field('port')} />
          </Stack>
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
            <TextField fullWidth label="Username" autoComplete="off" {...field('username')} />
            <TextField
              fullWidth
              label="Password"
              type="password"
              autoComplete="new-password"
              placeholder={settings?.password_set ? 'Saved — leave blank to keep it' : ''}
              InputLabelProps={settings?.password_set ? { shrink: true } : undefined}
              {...field('password')}
            />
          </Stack>

          {advanced && (
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField
                fullWidth
                label="Discovery prefix"
                helperText="Only if you changed it in Home Assistant’s MQTT settings"
                {...field('discovery_prefix')}
              />
              <TextField
                fullWidth
                label="Topic prefix"
                helperText="Give each Dapple its own if you run more than one"
                {...field('topic_prefix')}
              />
            </Stack>
          )}

          <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1 }}>
            <Button variant="contained" onClick={() => save()} disabled={busy}>
              Save
            </Button>
            {!advanced && <Button onClick={() => setAdvanced(true)}>Advanced</Button>}
            {settings?.password_set && (
              <Button color="inherit" onClick={() => save({ password: '' })} disabled={busy}>
                Remove saved password
              </Button>
            )}
          </Stack>
        </Stack>
      </Paper>
    </Stack>
  )
}
