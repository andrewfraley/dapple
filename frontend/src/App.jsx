import { useCallback, useEffect, useMemo, useState } from 'react'
import AppBar from '@mui/material/AppBar'
import Box from '@mui/material/Box'
import Container from '@mui/material/Container'
import IconButton from '@mui/material/IconButton'
import Tab from '@mui/material/Tab'
import Tabs from '@mui/material/Tabs'
import Toolbar from '@mui/material/Toolbar'
import Typography from '@mui/material/Typography'
import RefreshIcon from '@mui/icons-material/Refresh'

import * as api from './api.js'
import PatternPage from './PatternPage.jsx'
import StrandsPage from './StrandsPage.jsx'

const TABS = ['pattern', 'strands']

/** `#pattern/tree` → {tab: 'pattern', groupId: 'tree'}. */
function routeFromHash() {
  const [tab, groupId] = window.location.hash.replace('#', '').split('/')
  return { tab: TABS.includes(tab) ? tab : TABS[0], groupId: groupId || null }
}

/**
 * The shell: two pages, and the group list both of them work from.
 *
 * The tab and the selected group live in the URL hash, so Home Assistant can
 * link straight at `…:8080/#pattern/tree` and a reload comes back where you
 * were.
 */
export default function App() {
  const [route, setRoute] = useState(routeFromHash)
  const [groups, setGroups] = useState([])
  const [loaded, setLoaded] = useState(false)

  const reload = useCallback(async () => {
    try {
      setGroups(await api.getGroups())
    } catch {
      /* each page reports its own failures in its status line */
    } finally {
      setLoaded(true)
    }
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  useEffect(() => {
    const onHashChange = () => setRoute(routeFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const navigate = (tab, groupId) => {
    window.location.hash = groupId ? `${tab}/${groupId}` : tab
    setRoute({ tab, groupId: groupId || null })
  }

  // A hash naming a group that no longer exists shouldn't leave a blank page.
  const groupId = useMemo(() => {
    if (groups.some((group) => group.id === route.groupId)) return route.groupId
    return groups[0]?.id ?? null
  }, [groups, route.groupId])

  const recheck = async () => {
    await api.refreshDevices().catch(() => {})
    reload()
  }

  return (
    <Box sx={{ pb: 6 }}>
      <AppBar
        position="sticky"
        color="default"
        elevation={0}
        sx={{ borderBottom: 1, borderColor: 'divider' }}
      >
        <Toolbar variant="dense" sx={{ gap: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Box component="img" src="/favicon.svg" alt="" sx={{ width: 28, height: 28 }} />
            <Typography variant="h6">Dapple</Typography>
          </Box>
          <Tabs
            value={route.tab}
            onChange={(_event, tab) => navigate(tab, tab === 'pattern' ? groupId : null)}
            sx={{ flexGrow: 1, minHeight: 'auto' }}
          >
            <Tab label="Pattern" value="pattern" />
            <Tab label="Strands" value="strands" />
          </Tabs>
          <IconButton onClick={recheck} aria-label="Re-read strands">
            <RefreshIcon />
          </IconButton>
        </Toolbar>
      </AppBar>

      <Container maxWidth="md" sx={{ pt: 2 }}>
        {route.tab === 'strands' ? (
          <StrandsPage groups={groups} onChanged={reload} />
        ) : (
          <PatternPage
            groups={groups}
            groupId={groupId}
            loaded={loaded}
            onSelectGroup={(id) => navigate('pattern', id)}
            onChanged={reload}
          />
        )}
      </Container>
    </Box>
  )
}
