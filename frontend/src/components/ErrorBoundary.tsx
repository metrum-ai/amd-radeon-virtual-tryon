// Copyright Advanced Micro Devices, Inc.
//
// SPDX-License-Identifier: MIT

import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

// No error boundary existed anywhere in the tree, so any render-time throw unmounted the whole app to a blank page.
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled render error:', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{
          height: '100%', display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center', gap: 12,
          background: '#08080A', color: '#F0F0F0', fontFamily: 'sans-serif',
          textAlign: 'center', padding: 24,
        }}>
          <div style={{ fontSize: 18, fontWeight: 700 }}>Something went wrong.</div>
          <div style={{ fontSize: 13, color: '#9D9FA2', maxWidth: 480 }}>
            {this.state.error.message}
          </div>
          <button
            onClick={() => window.location.reload()}
            style={{
              marginTop: 8, padding: '8px 18px', borderRadius: 6, border: 'none',
              background: 'linear-gradient(135deg, #ED1C24, #F26522)', color: '#fff',
              fontWeight: 700, cursor: 'pointer',
            }}
          >
            Reload
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
