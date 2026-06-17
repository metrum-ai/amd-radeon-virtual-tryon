// Copyright Advanced Micro Devices, Inc.
// 
// SPDX-License-Identifier: MIT

export default function TopBar() {
  return (
    <div style={{
      height: 56,
      background: 'var(--bg-surface)',
      borderBottom: '1px solid var(--border)',
      display: 'flex',
      alignItems: 'center',
      padding: '0 20px',
      position: 'relative',
      flexShrink: 0,
      zIndex: 50,
    }}>
      {/* Animated gradient underline */}
      <div style={{
        position: 'absolute',
        bottom: 0,
        left: 0,
        right: 0,
        height: 2,
        background: 'linear-gradient(90deg, var(--amd-red) 0%, var(--amd-orange) 35%, transparent 70%)',
        animation: 'glowLine 4s ease-in-out infinite',
      }} />

      {/* Left — brand logo */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <img
          src="/assets/amd_logo.png"
          alt="AMD"
          style={{ height: 26, objectFit: 'contain', filter: 'brightness(0) invert(1)' }}
        />
      </div>

      {/* Center — product name */}
      <div style={{
        position: 'absolute',
        left: '50%',
        transform: 'translateX(-50%)',
      }}>
        <span style={{
          fontFamily: 'var(--font-condensed)',
          fontSize: 'var(--fs-lg)',
          fontWeight: 700,
          color: 'var(--text-primary)',
          letterSpacing: '0.14em',
          textTransform: 'uppercase',
          lineHeight: 1,
        }}>
          Virtual Try-On
        </span>
      </div>

      {/* Right — OpenClaw label */}
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{
          fontFamily: 'var(--font)',
          fontSize: 'var(--fs-xs)',
          color: 'var(--text-dim)',
          letterSpacing: '0.02em',
        }}>
          Powered by
        </span>
        <span style={{
          fontFamily: 'var(--font-condensed)',
          fontSize: 13,
          fontWeight: 700,
          background: 'var(--gradient-amd)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          letterSpacing: '0.06em',
        }}>
          OpenClaw
        </span>
      </div>
    </div>
  )
}
