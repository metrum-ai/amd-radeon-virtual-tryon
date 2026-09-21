// Copyright Advanced Micro Devices, Inc.
// 
// SPDX-License-Identifier: MIT

import { useState, useEffect, useMemo } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import type { Garment } from '../types/vto'

function Skel({ height, width = '100%', radius = 4 }: { height: number; width?: number | string; radius?: number }) {
  return (
    <div style={{
      height, width, borderRadius: radius, flexShrink: 0,
      background: 'linear-gradient(90deg, #1a1a1e 25%, #252528 50%, #1a1a1e 75%)',
      backgroundSize: '200% 100%',
      animation: 'shimmer 1.5s ease-in-out infinite',
    }} />
  )
}

interface ApiItem {
  item_id: string; name: string; category: string; subcategory?: string
  overlay_category: 'tops' | 'bottoms' | 'one-pieces'
  gender: 'women' | 'men' | 'unisex'
  brand?: string; color?: string; price: number; image_path: string
}

function apiToGarment(g: ApiItem): Garment {
  const GRADIENTS: Record<string, string> = {
    tops: 'linear-gradient(145deg, #1a1e2e, #252a3e)',
    bottoms: 'linear-gradient(145deg, #1a2a1e, #25352a)',
    'one-pieces': 'linear-gradient(145deg, #2a1a2e, #352535)',
  }
  const image = g.image_path.startsWith('/')
    ? g.image_path
    : `/${g.image_path}`
  return {
    id: g.item_id,
    name: g.name,
    category: g.overlay_category,
    overlayCategory: g.overlay_category,
    gender: g.gender ?? 'unisex',
    subcategory: g.subcategory ?? '',
    brand: g.brand ?? '',
    color: g.color ?? '',
    price: Number(g.price),
    gradient: GRADIENTS[g.overlay_category] ?? 'linear-gradient(145deg, #1e1e26, #2a2a36)',
    image,
    tags: [],
  }
}

interface SubcatDef { id: string; label: string }
interface SectionDef {
  id: string
  label: string
  overlayCategory: 'tops' | 'bottoms' | 'one-pieces'
  subcategories: SubcatDef[]
}

// Subcategory IDs match the `subcategory` column values seeded by init_db.py _CATEGORY_MAP
const MEN_SECTIONS: SectionDef[] = [
  {
    id: 'topwear', label: 'Topwear', overlayCategory: 'tops',
    subcategories: [
      { id: 't-shirt', label: 'T-Shirts' },
      { id: 'shirt', label: 'Casual Shirts' },
    ],
  },
  {
    id: 'bottomwear', label: 'Bottomwear', overlayCategory: 'bottoms',
    subcategories: [
      { id: 'pants_shorts', label: 'Pants & Shorts' },
    ],
  },
]

const WOMEN_SECTIONS: SectionDef[] = [
  {
    id: 'topwear', label: 'Topwear', overlayCategory: 'tops',
    subcategories: [
      { id: 'top', label: 'Tops' },
    ],
  },
  {
    id: 'bottomwear', label: 'Bottomwear', overlayCategory: 'bottoms',
    subcategories: [
      { id: 'pants_shorts', label: 'Pants & Shorts' },
    ],
  },
  {
    id: 'dresses', label: 'Dresses', overlayCategory: 'one-pieces',
    subcategories: [
      { id: 'maxi_dress', label: 'Maxi Dresses' },
    ],
  },
]

const GENDERS = [
  { id: 'women' as const, label: 'Women' },
  { id: 'men' as const, label: 'Men' },
]

interface Props {
  open: boolean
  onToggle: () => void
  activeCategory: string
  onCategoryChange: (c: string) => void
  selectedGarmentIds: string[]
  onSelectGarment: (g: Garment) => void
}

export default function CatalogBrowser({
  open, onToggle, activeCategory, onCategoryChange, selectedGarmentIds, onSelectGarment,
}: Props) {
  const [garments, setGarments] = useState<Garment[]>([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [activeGender, setActiveGender] = useState<'women' | 'men'>('women')
  const [activeSectionId, setActiveSectionId] = useState('topwear')
  const [activeSubcat, setActiveSubcat] = useState<string | null>(null)

  const sections = activeGender === 'men' ? MEN_SECTIONS : WOMEN_SECTIONS
  const activeSection = sections.find(s => s.id === activeSectionId) ?? sections[0]

  // Sync parent's activeCategory (overlay_category) → active section
  useEffect(() => {
    const match = sections.find(s => s.overlayCategory === activeCategory)
    if (match) {
      setActiveSectionId(match.id)
      setActiveSubcat(null)
    }
  }, [activeCategory])  // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch when section or gender changes
  useEffect(() => {
    setLoading(true)
    setActiveSubcat(null)
    const params = new URLSearchParams({ limit: '100', gender: activeGender })
    params.set('overlay_category', activeSection.overlayCategory)
    fetch(`/api/v1/vto/catalog?${params}`)
      .then(r => r.json())
      .then((data: ApiItem[]) => setGarments(data.map(apiToGarment)))
      .catch(() => setGarments([]))
      .finally(() => setLoading(false))
  }, [activeSection.overlayCategory, activeGender])

  // Subcategory IDs present in current data
  const availableSubcats = useMemo(() => {
    const seen = new Set<string>()
    garments.forEach(g => { if (g.subcategory) seen.add(g.subcategory) })
    return seen
  }, [garments])

  // Subcategory defs that have matching items in the current data
  const visibleSubcats = useMemo(
    () => activeSection.subcategories.filter(s => availableSubcats.has(s.id)),
    [activeSection.subcategories, availableSubcats],
  )

  // Client-side filtering
  const displayed = useMemo(() => {
    let result = garments
    if (activeSubcat) result = result.filter(g => g.subcategory === activeSubcat)
    if (search.trim()) {
      const q = search.toLowerCase()
      result = result.filter(g =>
        g.name.toLowerCase().includes(q) || g.brand.toLowerCase().includes(q),
      )
    }
    return result
  }, [garments, activeSubcat, search])

  function switchSection(section: SectionDef) {
    setActiveSectionId(section.id)
    setActiveSubcat(null)
    onCategoryChange(section.overlayCategory)
  }

  function switchGender(gender: 'women' | 'men') {
    setActiveGender(gender)
    const newSections = gender === 'men' ? MEN_SECTIONS : WOMEN_SECTIONS
    const match = newSections.find(s => s.id === activeSectionId) ?? newSections[0]
    setActiveSectionId(match.id)
    setActiveSubcat(null)
  }

  if (!open) {
    return (
      <div
        onClick={onToggle}
        style={{
          height: 36, flexShrink: 0,
          background: 'var(--surface)', borderTop: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          cursor: 'pointer', gap: 10,
          transition: 'background 0.15s ease',
        }}
        onMouseEnter={e => (e.currentTarget.style.background = 'var(--card)')}
        onMouseLeave={e => (e.currentTarget.style.background = 'var(--surface)')}
      >
        <ChevronDown size={14} color="var(--text-dim)" />
        <span style={{ fontFamily: 'var(--font-condensed)', fontSize: 12, fontWeight: 700, letterSpacing: '0.10em', textTransform: 'uppercase', color: 'var(--text-dim)' }}>
          Catalogue — {garments.length} items
        </span>
        <ChevronDown size={14} color="var(--text-dim)" />
      </div>
    )
  }

  return (
    <div style={{ flex: '0 1 270px', minHeight: 0, overflow: 'hidden', background: 'var(--bg-surface)', borderTop: '1px solid var(--border)', display: 'flex', flexDirection: 'column' }}>

      {/* ── Header ── */}
      <div style={{ padding: '6px 14px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
        {/* Collapse */}
        <div
          onClick={onToggle}
          style={{ width: 20, height: 20, borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', background: 'var(--elevated)', flexShrink: 0, transition: 'background 0.15s ease' }}
          onMouseEnter={e => (e.currentTarget.style.background = 'var(--border)')}
          onMouseLeave={e => (e.currentTarget.style.background = 'var(--elevated)')}
        >
          <ChevronUp size={10} color="var(--text-dim)" />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <div style={{ width: 3, height: 14, background: 'var(--gradient-amd)', borderRadius: 2 }} />
          <span style={{ fontFamily: 'var(--font-condensed)', fontSize: 'var(--fs-md)', fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-secondary)' }}>Catalog</span>
          <span style={{ fontFamily: 'var(--font)', fontSize: 'var(--fs-xs)', padding: '1px 7px', background: 'var(--accent-soft)', color: 'var(--amd-red)', borderRadius: 'var(--radius-xl)', fontWeight: 700, border: '1px solid rgba(237,28,36,0.15)' }}>
            {displayed.length} items
          </span>
        </div>

        {/* Gender tabs */}
        <div style={{ display: 'flex', gap: 3, background: 'var(--bg-elevated)', borderRadius: 'var(--radius-xl)', padding: 3, border: '1px solid var(--border)' }}>
          {GENDERS.map(g => (
            <button
              key={g.id}
              onClick={() => switchGender(g.id)}
              style={{
                fontFamily: 'var(--font)', padding: '3px 14px', borderRadius: 'var(--radius-xl)', fontSize: 'var(--fs-xs)', fontWeight: 700,
                background: activeGender === g.id ? 'var(--gradient-amd)' : 'transparent',
                color: activeGender === g.id ? '#fff' : 'var(--text-muted)',
                cursor: 'pointer', border: 'none', transition: 'all 0.15s ease',
              } as React.CSSProperties}
            >
              {g.label}
            </button>
          ))}
        </div>

        <div style={{ flex: 1 }} />

        {/* Search */}
        <div style={{ position: 'relative' }}>
          <input
            placeholder="Search catalog..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
              padding: '5px 10px 5px 28px', fontSize: 'var(--fs-xs)', color: 'var(--text-primary)', outline: 'none',
              fontFamily: 'var(--font)', width: 180,
            }}
            onFocus={e => { e.target.style.borderColor = 'var(--teal)'; e.target.style.boxShadow = '0 0 0 3px rgba(0,124,151,0.1)' }}
            onBlur={e => { e.target.style.borderColor = 'var(--border)'; e.target.style.boxShadow = 'none' }}
          />
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--text-dim)" strokeWidth="2" strokeLinecap="round" style={{ position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)' }}>
            <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
          </svg>
        </div>
      </div>

      {/* ── Section tabs ── */}
      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid var(--border)', flexShrink: 0, background: 'var(--bg-elevated)', overflowX: 'auto' }}>
        {sections.map(section => {
          const active = section.id === activeSectionId
          return (
            <button
              key={section.id}
              onClick={() => switchSection(section)}
              style={{
                padding: '8px 18px', border: 'none', cursor: 'pointer',
                fontFamily: 'var(--font)', fontSize: 12, fontWeight: 700,
                letterSpacing: '0.05em', textTransform: 'uppercase',
                color: active ? 'var(--amd-red)' : 'var(--text-muted)',
                background: 'transparent',
                borderBottom: active ? '2px solid var(--amd-red)' : '2px solid transparent',
                transition: 'all 0.15s ease',
                flexShrink: 0,
              } as React.CSSProperties}
              onMouseEnter={e => { if (!active) e.currentTarget.style.color = 'var(--text-secondary)' }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.color = 'var(--text-muted)' }}
            >
              {section.label}
            </button>
          )
        })}
      </div>

      {/* ── Subcategory pills ── */}
      <div style={{ display: 'flex', gap: 6, padding: '6px 14px', borderBottom: '1px solid var(--border)', flexShrink: 0, overflowX: 'auto', alignItems: 'center' }}>
        <SubcatPill label="All" active={activeSubcat === null} onClick={() => setActiveSubcat(null)} />
        {visibleSubcats.map(sub => (
          <SubcatPill
            key={sub.id}
            label={sub.label}
            active={activeSubcat === sub.id}
            onClick={() => setActiveSubcat(activeSubcat === sub.id ? null : sub.id)}
          />
        ))}
      </div>

      {/* ── Garment cards ── */}
      <div style={{ flex: 1, overflowX: 'auto', overflowY: 'hidden', display: 'flex', alignItems: 'stretch', gap: 8, padding: '8px 14px', minHeight: 0 }}>
        {loading ? (
          <>
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} style={{ width: 110, flexShrink: 0, borderRadius: 'var(--radius-md)', overflow: 'hidden', border: '1px solid var(--border)' }}>
                <Skel height={80} radius={0} />
                <div style={{ padding: '6px 7px', display: 'flex', flexDirection: 'column', gap: 5 }}>
                  <Skel height={10} width="85%" />
                  <Skel height={8} width="55%" />
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 2 }}>
                    <Skel height={8} width="40%" />
                    <Skel height={8} width="28%" />
                  </div>
                </div>
              </div>
            ))}
          </>
        ) : (
          <>
            {displayed.map(g => (
              <GarmentCard key={g.id} garment={g} isSelected={selectedGarmentIds.includes(g.id)} onSelect={onSelectGarment} />
            ))}
            {displayed.length === 0 && (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, color: 'var(--text-dim)', fontSize: 12 }}>
                {search ? `No items match "${search}"` : `No ${activeGender}'s items in this category`}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function SubcatPill({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '3px 10px', borderRadius: 'var(--radius-xl)', fontSize: 11, fontWeight: 600,
        fontFamily: 'var(--font)',
        background: active ? 'var(--gradient-amd)' : 'var(--bg-elevated)',
        color: active ? '#fff' : 'var(--text-muted)',
        border: active ? 'none' : '1px solid var(--border)',
        cursor: 'pointer', transition: 'all 0.15s ease', flexShrink: 0,
        whiteSpace: 'nowrap',
      } as React.CSSProperties}
    >
      {label}
    </button>
  )
}

function GarmentCard({ garment, isSelected, onSelect }: { garment: Garment; isSelected: boolean; onSelect: (g: Garment) => void }) {
  const [imgError, setImgError] = useState(false)

  return (
    <button
      onClick={() => onSelect(garment)}
      style={{
        width: 110, flexShrink: 0, display: 'flex', flexDirection: 'column', background: 'var(--card)',
        border: `1px solid ${isSelected ? 'var(--accent)' : 'var(--border)'}`,
        borderRadius: 'var(--radius-md)', overflow: 'hidden', cursor: 'pointer', textAlign: 'left',
        padding: 0, transition: 'all 0.15s ease',
        boxShadow: isSelected ? '0 0 0 1px rgba(237,28,36,0.3), var(--shadow-sm)' : 'none',
      }}
      onMouseEnter={e => { if (!isSelected) e.currentTarget.style.borderColor = 'var(--border-light)'; e.currentTarget.style.transform = 'translateY(-2px)' }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = isSelected ? 'var(--accent)' : 'var(--border)'; e.currentTarget.style.transform = 'translateY(0)' }}
    >
      {/* Image area */}
      <div style={{ height: 80, background: garment.gradient, position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, overflow: 'hidden' }}>
        {!imgError ? (
          <img
            src={garment.image}
            alt={garment.name}
            onError={() => setImgError(true)}
            style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
          />
        ) : (
          <span style={{ fontSize: 24, opacity: 0.6 }}>
            {garment.overlayCategory === 'tops' ? '👕' : garment.overlayCategory === 'bottoms' ? '👖' : '👗'}
          </span>
        )}
        {isSelected && (
          <div style={{ position: 'absolute', inset: 0, border: '2px solid var(--accent)', borderRadius: 'var(--radius-md)', pointerEvents: 'none' }} />
        )}
        <div style={{ position: 'absolute', top: 4, left: 4 }}>
          <span style={{ fontSize: 8, padding: '1px 5px', background: 'rgba(0,0,0,0.55)', color: 'rgba(255,255,255,0.7)', borderRadius: 'var(--radius-sm)', fontWeight: 600, letterSpacing: '0.04em' }}>
            {garment.overlayCategory}
          </span>
        </div>
        {isSelected && (
          <div style={{ position: 'absolute', top: 4, right: 4, width: 14, height: 14, borderRadius: '50%', background: 'var(--gradient-primary)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <svg width="8" height="8" viewBox="0 0 10 8" fill="none"><path d="M1 4L3.5 6.5L9 1" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
          </div>
        )}
      </div>

      {/* Info */}
      <div style={{ padding: '6px 7px', flex: 1 }}>
        <div style={{ fontFamily: 'var(--font)', fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1.3, marginBottom: 2, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
          {garment.name}
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 3 }}>
          <span style={{ fontFamily: 'var(--font)', fontSize: 9, color: 'var(--text-dim)' }}>{garment.brand}</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 700, color: 'var(--text-secondary)' }}>${garment.price}</span>
        </div>
      </div>
    </button>
  )
}
