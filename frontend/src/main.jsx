import React from 'react'
import { createRoot } from 'react-dom/client'
import CssBaseline from '@mui/material/CssBaseline'
import { ThemeProvider, createTheme } from '@mui/material/styles'

import App from './App.jsx'

const PRIMARY = '#ffb74d'

// Dark only: the preview is lights on a dark panel, and a white page around it
// fights that. Built once, since nothing about it changes at runtime.
const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: PRIMARY },
    // MUI's default of 3 picks white text on the dark-mode error red (3.7:1);
    // 4.5 is the WCAG AA line for normal text and flips it to black.
    contrastThreshold: 4.5,
    // MUI greys disabled controls to 30% white, about 2.7:1 on this page and
    // unreadable on a phone outdoors. 55% clears 4.5:1 on the page and on a
    // disabled contained button, and is still plainly dimmer than live text.
    text: { disabled: 'rgba(255, 255, 255, 0.55)' },
    action: { disabled: 'rgba(255, 255, 255, 0.55)' },
  },
  shape: { borderRadius: 10 },
  components: {
    // The default keyboard focus cue on buttons and tabs is a faint ripple that
    // is easy to lose on a dark page.
    MuiButtonBase: {
      styleOverrides: {
        root: {
          '&.Mui-focusVisible': { outline: `2px solid ${PRIMARY}`, outlineOffset: 2 },
        },
      },
    },
  },
})

function Root() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <App />
    </ThemeProvider>
  )
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
)
