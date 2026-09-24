import { useEffect, useState } from 'react'
import Box from '@mui/material/Box'
import Link from '@mui/material/Link'
import Typography from '@mui/material/Typography'

import * as api from './api.js'
import { REPO_URL } from './links.js'

/**
 * The running version and where the code lives. The version comes from the
 * server, not the build, so it's the one to quote in a bug report.
 */
export default function Footer() {
  const [version, setVersion] = useState(null)

  useEffect(() => {
    api
      .ping()
      .then((body) => setVersion(body?.version ?? null))
      .catch(() => {
        /* the footer just goes without a version */
      })
  }, [])

  return (
    <Box component="footer" sx={{ mt: 4, textAlign: 'center' }}>
      <Typography variant="caption" sx={{ color: 'text.secondary' }}>
        Dapple{version && ` ${version}`} ·{' '}
        <Link href={REPO_URL} target="_blank" rel="noreferrer" color="inherit">
          GitHub
        </Link>
      </Typography>
    </Box>
  )
}
