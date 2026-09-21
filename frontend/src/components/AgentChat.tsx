// Copyright Advanced Micro Devices, Inc.
// 
// SPDX-License-Identifier: MIT

import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Send, Mic } from 'lucide-react'
import type { CustomerProfile, Garment, Message, PipelineCompletion } from '../types/vto'
import { useAgentSpeech } from '../hooks/useAgentSpeech'
import { safeRandomUUID } from '../lib/uuid'

// Agent config — set `avatar` to the filename stem (no path, no .png) of the image in
// frontend/public/assets/avatars/. Drop a new PNG there and update avatar to match.
interface AgentConfig {
  label: string
  initials: string
  color: string
  avatar?: string
}

// Module-level build stamp — busts browser avatar cache on every rebuild
const AVATAR_V = Date.now()

const AGENT_CONFIG: Record<string, AgentConfig> = {
  TryOnOrchestrator:    { label: 'TryOnOrchestrator', initials: 'TO', color: '#ed1c24' },
  FashionStylistAgent:  { label: 'FashionStylist',    initials: 'FS', color: '#7c3aed', avatar: 'Fashion-Stylist' },
  LogisticsAgent:       { label: 'LogisticsAgent',    initials: 'LA', color: '#d97706', avatar: 'Logistics-Agent' },
  CustomerFeedbackAgent:{ label: 'CustomerFeedback',  initials: 'CF', color: '#059669', avatar: 'Customer-Feedback' },
}
const DEFAULT_AGENT = 'FashionStylistAgent'

const AGENT_CHAT_URL = '/api/v1/vto/agent/chat'
const CUSTOMER_PROFILES = [
  { value: '', label: 'Select Customer', prompt: '', gender: undefined },
  { value: 'female', label: 'Female Customer', prompt: 'female store customer shopping for women-focused styling', gender: 'women' },
  { value: 'male', label: 'Male Customer', prompt: 'male store customer shopping for men-focused styling', gender: 'men' },
  { value: 'female-senior', label: 'Female Senior Customer', prompt: 'female senior store customer shopping for women-focused styling', gender: 'women' },
  { value: 'male-senior', label: 'Male Senior Customer', prompt: 'male senior store customer shopping for men-focused styling', gender: 'men' },
] as const
type CustomerGender = 'women' | 'men' | undefined

interface ApiSearchResult {
  item_id: string
  name: string
  category: string
  overlay_category: 'tops' | 'bottoms' | 'one-pieces'
  gender?: 'women' | 'men' | 'unisex'
  brand?: string
  color?: string
  price?: number
  image_path: string
}

const AGENT_MODEL: Record<string, string> = {
  LogisticsAgent: 'openclaw/logistics-agent',
  FashionStylistAgent: 'openclaw/fashion-stylist',
  TryOnOrchestrator: 'openclaw/tryon-orchestrator',
  CustomerFeedbackAgent: 'openclaw/customer-feedback-agent',
}

const FEEDBACK_KEYWORDS = ['leave feedback', 'give feedback', 'rate my experience', 'submit feedback', 'leave a review', 'rate the try-on', 'rate this try-on']
const LOGISTICS_KEYWORDS = ['price', 'stock', 'branch', 'availab', 'ship', 'delivery', 'pickup']
const AGENT_DECORATIVE_SYMBOLS = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\uFE0F]/gu
const AGENT_UNAVAILABLE_MESSAGE = 'Agent temporarily unavailable. Please try again.'

function agentFor(input: string): string {
  const l = input.toLowerCase()
  if (FEEDBACK_KEYWORDS.some(k => l.includes(k))) return 'CustomerFeedbackAgent'
  if (LOGISTICS_KEYWORDS.some(k => l.includes(k))) return 'LogisticsAgent'
  return DEFAULT_AGENT
}

function apiToGarment(g: ApiSearchResult): Garment {
  const gradients: Record<string, string> = {
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
    category: g.category as Garment['category'],
    overlayCategory: g.overlay_category,
    gender: g.gender ?? 'unisex',
    subcategory: '',
    brand: g.brand ?? '',
    color: g.color ?? '',
    price: Number(g.price ?? 0),
    gradient: gradients[g.overlay_category] ?? 'linear-gradient(145deg, #1e1e26, #2a2a36)',
    image,
    tags: [],
  }
}

// Words that appear in header sentences but never in garment names
const _SENTENCE_WORDS = /\b(is|are|was|were|be|been|has|have|had|do|does|did|will|would|can|could|these|those|from|with|and|for|the|complete|outfit|logistics|options|panel|catalog|here|a)\b/i

function parseRecommendationNames(text: string): string[] {
  const names = new Set<string>()
  for (const line of text.split('\n')) {
    // Allow hyphens, apostrophes, digits in garment names (e.g. "Wide-Leg Trousers", "High-Waist")
    const match = line.match(/^\s*(?:[-•*]\s*)?([A-Z][A-Za-z0-9'\- ]+?)\s*:/)
    if (match) {
      const name = match[1].trim()
      // Skip price lines, sentence headers ("Here is a complete outfit..."), and short matches
      if (name.length > 4 && !/^\$/.test(name) && !_SENTENCE_WORDS.test(name)) {
        names.add(name)
      }
    }
  }
  return [...names].slice(0, 8)
}

function genderForProfile(customerProfile: CustomerProfile): CustomerGender {
  return CUSTOMER_PROFILES.find(item => item.value === customerProfile)?.gender
}

function genderMatches(garmentGender: string | undefined, gender: CustomerGender): boolean {
  if (!gender) return true
  return garmentGender === gender || garmentGender === 'unisex'
}

// Scan for the first balanced {...} object that contains a "recommendations"
// key. Brace-aware (regex can't balance braces), so it tolerates prose before
// or after the JSON and nested objects in the recommendations array.
function findRecommendationObject(text: string): string | null {
  let searchFrom = 0
  while (true) {
    const start = text.indexOf('{', searchFrom)
    if (start === -1) return null
    let depth = 0
    let inString = false
    let escaped = false
    for (let i = start; i < text.length; i++) {
      const ch = text[i]
      if (inString) {
        if (escaped) escaped = false
        else if (ch === '\\') escaped = true
        else if (ch === '"') inString = false
        continue
      }
      if (ch === '"') inString = true
      else if (ch === '{') depth++
      else if (ch === '}') {
        depth--
        if (depth === 0) {
          const candidate = text.slice(start, i + 1)
          if (candidate.includes('"recommendations"')) return candidate
          break // not a recommendation object; resume scanning after it
        }
      }
    }
    searchFrom = start + 1
  }
}

function extractRecommendationJson(text: string): { recommendations?: unknown[] } | null {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i)
  if (fenced) {
    try { return JSON.parse(fenced[1]) } catch { /* fall through */ }
  }
  try {
    const parsed = JSON.parse(text.trim())
    if (parsed && typeof parsed === 'object') return parsed
  } catch { /* fall through */ }
  // Handle JSON embedded anywhere in prose (before, after, or mid-message).
  const inline = findRecommendationObject(text)
  if (inline) {
    try { return JSON.parse(inline) } catch { /* fall through */ }
  }
  return null
}

// Heuristic: content that mentions recommendation payload keys is a (possibly
// malformed or truncated) recommendation attempt, not conversational prose.
function looksLikeRecommendationAttempt(text: string): boolean {
  return text.includes('"recommendations"') || text.includes('"item_id"')
}

function parseRecommendationIds(text: string): string[] {
  const parsed = extractRecommendationJson(text)
  if (!parsed) return []
  const rawItems = Array.isArray(parsed) ? parsed : parsed.recommendations
  if (!Array.isArray(rawItems)) return []

  return [...new Set(
    rawItems
      .map(item => typeof item === 'string' ? item : (item as Record<string, unknown>)?.item_id)
      .filter((itemId): itemId is string =>
        typeof itemId === 'string' && itemId.length > 0
      )
  )]
    .slice(0, 8)
}

async function resolveRecommendations(text: string, gender: CustomerGender): Promise<Garment[]> {
  const garments: Garment[] = []
  const itemIds = parseRecommendationIds(text)

  for (const itemId of itemIds) {
    const res = await fetch(`/api/v1/vto/catalog/${itemId}`)
    if (!res.ok) continue
    const item = (await res.json()) as ApiSearchResult
    if (genderMatches(item.gender, gender) && !garments.some(g => g.id === item.item_id)) {
      garments.push(apiToGarment(item))
    }
  }

  if (garments.length > 0) return garments

  const names = parseRecommendationNames(text)

  for (const name of names) {
    const res = await fetch('/api/v1/vto/catalog/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: name, top_k: 60, filters: { gender } }),
    })
    if (!res.ok) continue
    const results = (await res.json()) as ApiSearchResult[]
    const exact = results.find(r =>
      r.name.toLowerCase() === name.toLowerCase() && genderMatches(r.gender, gender)
    )
    if (exact && !garments.some(g => g.id === exact.item_id)) {
      garments.push(apiToGarment(exact))
    }
  }

  return garments
}

function recommendationDisplayText(recommendations: Garment[]): string {
  if (recommendations.length === 0) return ''
  return [
    'Here are catalog-backed picks:',
    ...recommendations.map(item =>
      `- ${item.name}: ${[item.color, item.category].filter(Boolean).join(' · ')}`
    ),
  ].join('\n')
}

function isEmptyRecommendationPayload(text: string): boolean {
  try {
    const parsed = JSON.parse(text.trim())
    return Array.isArray(parsed?.recommendations) && parsed.recommendations.length === 0
  } catch {
    return false
  }
}

function isRecommendationPayload(text: string): boolean {
  const parsed = extractRecommendationJson(text)
  return Array.isArray(parsed?.recommendations)
}

function agentDisplayText(content: string, recommendations: Garment[]): string {
  if (recommendations.length > 0) {
    return recommendationDisplayText(recommendations)
  }
  if (isEmptyRecommendationPayload(content)) {
    return 'I could not find a catalog-backed match for that request. Try a color, category, or occasion with a bit more detail.'
  }
  // Any recommendation-shaped output (valid, malformed, or truncated) reaching
  // here failed to resolve — show the fallback rather than leaking raw JSON.
  if (
    isRecommendationPayload(content) ||
    looksLikeRecommendationAttempt(content) ||
    content.includes('<catalog_context>')
  ) {
    return 'I could not resolve those recommendations to real catalog items. Try a color, category, or occasion with a bit more detail.'
  }
  return cleanAgentContent(content)
}

function cleanAgentContent(content: string): string {
  return content
    .replace(/<catalog_context>[\s\S]*?<\/catalog_context>/gi, '')
    .replace(/<\/?catalog_context>/gi, '')
    .replace(/<think>[\s\S]*?<\/think>/gi, '')
    .replace(/\{[\s\S]*?"recommendations"[\s\S]*?\}\s*$/g, '')
    .replace(AGENT_DECORATIVE_SYMBOLS, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/[ \t]{2,}/g, ' ')
    .trim()
}

function streamErrorMessage(chunk: unknown): string | null {
  if (!chunk || typeof chunk !== 'object') return null
  const record = chunk as Record<string, unknown>
  if (record.type === 'error' && typeof record.message === 'string') {
    return record.message
  }
  if (typeof record.error === 'string') return record.error
  if (record.error && typeof record.error === 'object') {
    const error = record.error as Record<string, unknown>
    if (typeof error.message === 'string') return error.message
  }
  const choices = record.choices
  if (Array.isArray(choices)) {
    const firstChoice = choices[0] as Record<string, unknown> | undefined
    if (firstChoice?.finish_reason === 'error') {
      return 'OpenClaw ended the stream with an error.'
    }
  }
  return null
}

function streamTextDelta(chunk: unknown): string {
  if (!chunk || typeof chunk !== 'object') return ''
  const record = chunk as Record<string, unknown>
  if (
    record.type === 'response.output_text.delta' &&
    typeof record.delta === 'string'
  ) {
    return record.delta
  }
  const choices = record.choices
  if (!Array.isArray(choices)) return ''
  const firstChoice = choices[0] as Record<string, unknown> | undefined
  const delta = firstChoice?.delta as Record<string, unknown> | undefined
  return typeof delta?.content === 'string' ? delta.content : ''
}

function TypewriterText({ text, isLatest }: { text: string; isLatest: boolean }) {
  const [displayed, setDisplayed] = useState(isLatest ? '' : text)
  const [done, setDone] = useState(!isLatest)

  useEffect(() => {
    if (!isLatest) {
      setDisplayed(text)
      setDone(true)
      return
    }
    setDisplayed('')
    setDone(false)
    let i = 0
    const speed = Math.min(text.length * 28, 8000) / Math.max(text.length, 1)
    const timer = setInterval(() => {
      i++
      setDisplayed(text.slice(0, i))
      if (i >= text.length) {
        clearInterval(timer)
        setDone(true)
      }
    }, speed)
    return () => clearInterval(timer)
  }, [text, isLatest])

  if (done) return <MarkdownContent text={text} />

  return (
    <span style={{ fontFamily: 'var(--font)', fontSize: 15, color: 'rgba(255,255,255,0.82)', lineHeight: 1.55, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
      {displayed}
      <span style={{ display: 'inline-block', width: 2, height: '0.9em', background: 'var(--teal)', verticalAlign: 'text-bottom', marginLeft: 1, animation: 'blink-cursor 0.7s ease-in-out infinite' }} />
    </span>
  )
}

// Allow only safe link schemes in agent-rendered markdown. Returns the href
// when safe, or undefined to suppress the link (e.g. javascript:/data: URLs).
function sanitizeHref(href: unknown): string | undefined {
  if (typeof href !== 'string') return undefined
  const raw = href.trim()
  if (!raw) return undefined
  // Browsers strip whitespace/control chars before resolving the scheme, so
  // detect the scheme on a stripped copy to catch e.g. "java\tscript:".
  const stripped = raw.replace(/[\u0000-\u0020]+/g, '')
  const scheme = stripped.match(/^([a-zA-Z][a-zA-Z0-9+.-]*):/)
  if (scheme && !['http', 'https', 'mailto', 'tel'].includes(scheme[1].toLowerCase())) {
    return undefined
  }
  return raw
}

function MarkdownContent({ text }: { text: string }) {
  const clean = text
    .replace(/^\*{3,}$/gm, '')
    .trim()

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ children, href, ...props }) => {
          const safe = sanitizeHref(href)
          if (!safe) {
            return <span style={{ color: 'var(--teal)' }}>{children}</span>
          }
          return (
            <a
              {...props}
              href={safe}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: 'var(--teal)', textDecoration: 'none' }}
            >
              {children}
            </a>
          )
        },
        code: ({ children, ...props }) => (
          <code
            {...props}
            style={{
              background: 'rgba(255,255,255,0.06)',
              borderRadius: 4,
              color: 'var(--text-primary)',
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
              padding: '1px 4px',
            }}
          >
            {children}
          </code>
        ),
        li: ({ children }) => <li style={{ margin: '3px 0' }}>{children}</li>,
        ol: ({ children }) => (
          <ol style={{ margin: '4px 0 4px 18px', padding: 0 }}>{children}</ol>
        ),
        p: ({ children }) => <p style={{ margin: '0 0 6px' }}>{children}</p>,
        pre: ({ children }) => (
          <pre
            style={{
              background: 'rgba(0,0,0,0.22)',
              border: '1px solid var(--border)',
              borderRadius: 8,
              margin: '6px 0',
              overflowX: 'auto',
              padding: 8,
            }}
          >
            {children}
          </pre>
        ),
        strong: ({ children }) => (
          <strong style={{ color: 'var(--text-primary)' }}>{children}</strong>
        ),
        table: ({ children }) => (
          <div style={{ margin: '6px 0', overflowX: 'auto' }}>
            <table
              style={{
                borderCollapse: 'collapse',
                minWidth: '100%',
                fontSize: 12,
              }}
            >
              {children}
            </table>
          </div>
        ),
        td: ({ children }) => (
          <td style={{ borderTop: '1px solid var(--border)', padding: '5px 7px' }}>
            {children}
          </td>
        ),
        th: ({ children }) => (
          <th
            style={{
              color: 'var(--text-primary)',
              padding: '5px 7px',
              textAlign: 'left',
            }}
          >
            {children}
          </th>
        ),
        ul: ({ children }) => (
          <ul style={{ margin: '4px 0 4px 18px', padding: 0 }}>{children}</ul>
        ),
      }}
    >
      {clean}
    </ReactMarkdown>
  )
}

async function callOpenClaw(
  agentKey: string,
  history: Message[],
  userText: string,
  sessionId: string,
  customerProfile: CustomerProfile,
  selectedGarment?: Garment | null,
): Promise<string> {
  const model = AGENT_MODEL[agentKey] ?? 'openclaw/default'
  const profile = CUSTOMER_PROFILES.find(item => item.value === customerProfile)
  const gender = genderForProfile(customerProfile)
  const apiMessages = [
    {
      role: 'system',
      content: [
        `Shopper profile: ${profile?.prompt ?? 'selected store customer'}.`,
        gender ? `Use catalog gender_filter="${gender}" and include only ${gender} or unisex garments.` : '',
        selectedGarment
          ? `Selected catalog item: item_id="${selectedGarment.id}", name="${selectedGarment.name}", color="${selectedGarment.color}", category="${selectedGarment.category}".`
          : '',
      ].filter(Boolean).join(' '),
    },
    ...history.slice(-10).map(m => ({
      role: m.role === 'user' ? 'user' : 'assistant',
      content: m.content,
    })),
    { role: 'user', content: userText },
  ]
  const res = await fetch(AGENT_CHAT_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      messages: apiMessages,
      stream: true,
      session_id: sessionId,
      customer_gender: gender,
      user: sessionId,
    }),
  })
  if (!res.ok) throw new Error(`OpenClaw ${res.status}`)

  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let content = ''
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const payload = line.slice(6).trim()
      if (payload === '[DONE]') continue
      try {
        const chunk = JSON.parse(payload)
        const errorMessage = streamErrorMessage(chunk)
        if (errorMessage) throw new Error(errorMessage)
        content += streamTextDelta(chunk)
      } catch (error) {
        if (error instanceof SyntaxError) continue
        throw error
      }
    }
  }
  if (!content.trim()) throw new Error('OpenClaw returned an empty response')
  return content
}

const SpeechRecognitionAPI: typeof SpeechRecognition | null =
  (typeof window !== 'undefined' &&
    (window.SpeechRecognition || (window as Window & { webkitSpeechRecognition?: typeof SpeechRecognition }).webkitSpeechRecognition)) || null

// The constructor above exists even outside a secure context, but actually starting it fails
// silently-ish since mic access needs https:/localhost/127.0.0.1 — check isSecureContext up front instead.
const MIC_BLOCKED_INSECURE_CONTEXT = typeof window !== 'undefined' && !window.isSecureContext

export default function AgentChat({
  onRecommendations,
  onNewSession,
  sessionId,
  customerProfile,
  onCustomerProfileChange,
  completedPipeline,
  selectedGarment,
}: {
  onRecommendations?: (items: Garment[]) => void
  onNewSession?: () => void
  sessionId: string
  customerProfile: CustomerProfile
  onCustomerProfileChange: (value: CustomerProfile) => void
  completedPipeline?: PipelineCompletion | null
  selectedGarment?: Garment | null
}) {
  const [messages, setMessages] = useState<Message[]>([])
  const [lastAgentMsgId, setLastAgentMsgId] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [interimTranscript, setInterimTranscript] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const [typingAgent, setTypingAgent] = useState<string>(DEFAULT_AGENT)
  const [showTextInput, setShowTextInput] = useState(false)
  const [micActive, setMicActive] = useState(false)
  const [hasShownRecs, setHasShownRecs] = useState(false)
  const [feedbackMessageId, setFeedbackMessageId] = useState<string | null>(null)
  const feedbackJobIdRef = useRef<string | null>(null)
  const welcomedProfileRef = useRef<CustomerProfile>('')
  // Bumped whenever the conversation is reset (new session / profile change);
  // an in-flight turn whose epoch no longer matches is discarded so stale
  // responses cannot leak into a fresh conversation.
  const epochRef = useRef(0)
  // Increments per send so only the most recent turn may drive the styling
  // panel — an earlier, slower response can't clobber newer recommendations.
  const reqSeqRef = useRef(0)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const recognitionRef = useRef<SpeechRecognition | null>(null)
  const finalTranscriptRef = useRef('')
  const micWarnedRef = useRef(false)
  const { isMuted, isSpeaking, setIsMuted, speak, stop: stopSpeech } = useAgentSpeech()
  const canUseInput = Boolean(customerProfile) && !isTyping

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isTyping])

  useEffect(() => {
    if (showTextInput) inputRef.current?.focus()
  }, [showTextInput])

  useEffect(() => {
    if (!completedPipeline) return
    if (feedbackJobIdRef.current === completedPipeline.jobId) return
    feedbackJobIdRef.current = completedPipeline.jobId
    const garmentName = completedPipeline.garment.name
    const cfFallback = `Your try-on video for the ${garmentName} is ready! Would you like to take 30 seconds to rate how it looked? Totally optional.`
    const isCfRefusal = (s: string) => s.trim().length < 20 || /^(no|n\/a|sorry|i cannot|i can't)\.?$/i.test(s.trim())

    void callOpenClaw(
      'CustomerFeedbackAgent',
      [],
      `Your try-on video for the ${garmentName} is ready. In one or two warm sentences, invite the shopper to optionally rate their experience.`,
      sessionId,
      customerProfile,
      selectedGarment,
    ).then((nudge) => {
      const msgId = `fb${Date.now()}`
      const safeNudge = isCfRefusal(nudge) ? cfFallback : nudge
      setFeedbackMessageId(msgId)
      setLastAgentMsgId(msgId)
      setMessages(prev => [...prev, {
        id: msgId, role: 'agent', agent: 'CustomerFeedbackAgent', content: safeNudge,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      }])
      void speak(safeNudge)
    }).catch(() => {
      const msgId = `fb${Date.now()}`
      setFeedbackMessageId(msgId)
      setLastAgentMsgId(msgId)
      setMessages(prev => [...prev, {
        id: msgId, role: 'agent', agent: 'CustomerFeedbackAgent', content: cfFallback,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      }])
      void speak(cfFallback)
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [completedPipeline])

  useEffect(() => {
    if (!customerProfile || welcomedProfileRef.current === customerProfile) return
    welcomedProfileRef.current = customerProfile
    setTypingAgent('FashionStylistAgent')
    setIsTyping(true)
    void callOpenClaw(
      'FashionStylistAgent',
      [],
      'Welcome the selected store customer to the virtual try-on experience. One warm sentence, then ask what style or occasion they are shopping for. Use plain text only; do not use emojis or decorative symbols.',
      sessionId,
      customerProfile,
      selectedGarment,
    ).then((content) => {
      const wId = `w${Date.now()}`
      const displayContent = cleanAgentContent(content)
      setLastAgentMsgId(wId)
      setMessages([{
        id: wId, role: 'agent', agent: 'FashionStylistAgent', content: displayContent,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      }])
      void speak(displayContent)
    }).catch(() => {
      const wId = `w${Date.now()}`
      setLastAgentMsgId(wId)
      setMessages([{
        id: wId, role: 'agent', agent: 'FashionStylistAgent',
        content: 'Welcome to virtual try-on. What style or occasion are you shopping for today?',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      }])
    }).finally(() => setIsTyping(false))
  }, [customerProfile, selectedGarment, sessionId, speak])

  const handleSend = useCallback(async () => {
    const trimmed = input.trim()
    if (!trimmed || !canUseInput) return

    const startedEpoch = epochRef.current
    const startedSeq = ++reqSeqRef.current

    const userMsg: Message = {
      id: `m${Date.now()}`, role: 'user', content: trimmed,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    }
    setMessages(prev => [...prev, userMsg])
    setInput('')

    const respondingAgent = agentFor(trimmed)
    setTypingAgent(respondingAgent)
    setIsTyping(true)

    try {
      const content = await callOpenClaw(
        respondingAgent,
        messages,
        trimmed,
        sessionId,
        customerProfile,
        selectedGarment,
      )
      const isLogistics = respondingAgent === 'LogisticsAgent'
      const recommendations = isLogistics ? [] : await resolveRecommendations(content, genderForProfile(customerProfile))
      // Conversation was reset while this turn was in flight — drop it.
      if (epochRef.current !== startedEpoch) return
      const gotRecs = recommendations.length > 0
      const displayContent = isLogistics ? content : agentDisplayText(content, recommendations)
      // Only the latest in-flight turn may update the styling panel.
      if (gotRecs && startedSeq === reqSeqRef.current) {
        onRecommendations?.(recommendations)
        setHasShownRecs(true)
      }
      setIsTyping(false)
      const rId = `r${Date.now()}`
      setLastAgentMsgId(rId)
      setMessages(prev => [...prev, {
        id: rId, role: 'agent', agent: respondingAgent, content: displayContent,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      }])
      void speak(displayContent)

    } catch {
      if (epochRef.current !== startedEpoch) return
      setIsTyping(false)
      const rId = `r${Date.now()}`
      setLastAgentMsgId(rId)
      setMessages(prev => [...prev, {
        id: rId, role: 'agent', agent: respondingAgent,
        content: AGENT_UNAVAILABLE_MESSAGE,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      }])
    }
  }, [canUseInput, customerProfile, hasShownRecs, input, messages, onRecommendations, selectedGarment, sessionId, speak])

  function handleNewSession() {
    epochRef.current += 1
    stopRecognition()
    stopSpeech()
    setMessages([])
    setLastAgentMsgId(null)
    setInput('')
    setInterimTranscript('')
    setIsTyping(false)
    onCustomerProfileChange('')
    setHasShownRecs(false)
    setFeedbackMessageId(null)
    welcomedProfileRef.current = ''
    feedbackJobIdRef.current = null
    onRecommendations?.([])
    onNewSession?.()
  }

  function handleCustomerProfileChange(value: CustomerProfile) {
    epochRef.current += 1
    stopRecognition()
    stopSpeech()
    setMessages([])
    setLastAgentMsgId(null)
    setInput('')
    setInterimTranscript('')
    setIsTyping(false)
    setHasShownRecs(false)
    setFeedbackMessageId(null)
    welcomedProfileRef.current = ''
    feedbackJobIdRef.current = null
    onRecommendations?.([])
    onCustomerProfileChange(value)
  }

  function stopRecognition() {
    recognitionRef.current?.stop()
    recognitionRef.current = null
    setMicActive(false)
    setInterimTranscript('')
  }

  function sendVoiceText(text: string) {
    if (!text.trim() || !canUseInput) return
    const trimmed = text.trim()
    const startedEpoch = epochRef.current
    const startedSeq = ++reqSeqRef.current
    setMessages(prev => [...prev, {
      id: `vm-${Date.now()}`, role: 'user', content: trimmed,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    }])
    const agent = agentFor(trimmed)
    setTypingAgent(agent)
    setIsTyping(true)
    callOpenClaw(agent, messages, trimmed, sessionId, customerProfile, selectedGarment)
      .then(async (content) => {
        const isLogisticsAgent = agent === 'LogisticsAgent'
        const recommendations = isLogisticsAgent ? [] : await resolveRecommendations(content, genderForProfile(customerProfile))
        // Conversation was reset while this turn was in flight — drop it.
        if (epochRef.current !== startedEpoch) return
        const gotRecs = recommendations.length > 0
        const displayContent = isLogisticsAgent ? content : agentDisplayText(content, recommendations)
        // Only the latest in-flight turn may update the styling panel.
        if (gotRecs && startedSeq === reqSeqRef.current) {
          onRecommendations?.(recommendations)
          setHasShownRecs(true)
        }
        setIsTyping(false)
        const vaId = `va-${Date.now()}`
        setLastAgentMsgId(vaId)
        setMessages(prev => [...prev, {
          id: vaId, role: 'agent', agent, content: displayContent,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        }])
        void speak(displayContent)
      })
      .catch(() => {
        if (epochRef.current !== startedEpoch) return
        setIsTyping(false)
        const vaId = `va-${Date.now()}`
        setLastAgentMsgId(vaId)
        setMessages(prev => [...prev, {
          id: vaId, role: 'agent', agent, content: AGENT_UNAVAILABLE_MESSAGE,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        }])
      })
  }

  function handleMicToggle() {
    if (!canUseInput) return
    if (micActive) {
      stopRecognition()
      return
    }

    if (!SpeechRecognitionAPI) {
      // Browser doesn't support STT — fall back to text input
      setShowTextInput(true)
      return
    }

    if (MIC_BLOCKED_INSECURE_CONTEXT) {
      setShowTextInput(true)
      if (!micWarnedRef.current) {
        micWarnedRef.current = true
        setMessages(prev => [...prev, {
          id: `r${Date.now()}`, role: 'agent', agent: 'FashionStylistAgent',
          content: 'Voice input needs HTTPS or localhost — please use the text box instead.',
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        }])
      }
      return
    }

    setShowTextInput(false)
    finalTranscriptRef.current = ''

    const recognition = new SpeechRecognitionAPI()
    recognition.continuous = false
    recognition.interimResults = true
    recognition.lang = 'en-US'

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let interim = ''
      let final = ''
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const t = event.results[i][0].transcript
        if (event.results[i].isFinal) final += t
        else interim += t
      }
      if (final) finalTranscriptRef.current += final
      setInterimTranscript(interim || finalTranscriptRef.current)
    }

    recognition.onend = () => {
      setMicActive(false)
      setInterimTranscript('')
      const spoken = finalTranscriptRef.current.trim()
      finalTranscriptRef.current = ''
      recognitionRef.current = null
      if (spoken) sendVoiceText(spoken)
    }

    recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
      if (event.error !== 'no-speech') console.warn('STT error:', event.error)
      stopRecognition()
    }

    recognitionRef.current = recognition
    recognition.start()
    setMicActive(true)
  }

  return (
    <div style={{ flex: 2, display: 'flex', flexDirection: 'column', background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', minWidth: 0, overflow: 'hidden' }}>
      {/* Header */}
      <div style={{ padding: '12px 16px', background: 'linear-gradient(180deg, var(--bg-surface), var(--bg-card))', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexShrink: 0 }}>
        {/* Left: title + action buttons */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ width: 4, height: 20, background: 'var(--gradient-amd)', borderRadius: 3, flexShrink: 0 }} />
            <span style={{ fontFamily: 'var(--font-condensed)', fontSize: 16, fontWeight: 800, letterSpacing: '0.10em', textTransform: 'uppercase', color: 'var(--text-primary)' }}>Style Desk</span>
          </div>
          <div style={{ paddingLeft: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              onClick={() => {
                if (isSpeaking) stopSpeech()
                setIsMuted(v => !v)
              }}
              title={isMuted ? 'Unmute agent voice' : 'Mute agent voice'}
              style={{
                border: '1px solid var(--border-light)',
                background: isMuted ? 'rgba(30,30,38,0.85)' : 'rgba(0,124,151,0.12)',
                color: isMuted ? 'var(--text-dim)' : 'var(--teal)',
                borderRadius: 6,
                padding: '5px 10px',
                fontSize: 11,
                fontWeight: 700,
                cursor: 'pointer',
              }}
            >
              {isMuted ? 'Voice Off' : isSpeaking ? 'Speaking' : 'Voice On'}
            </button>
            <button
              onClick={handleNewSession}
              style={{
                border: '1px solid rgba(237,28,36,0.35)',
                background: 'rgba(237,28,36,0.08)',
                color: 'var(--amd-red)',
                borderRadius: 6,
                padding: '5px 10px',
                fontSize: 11,
                fontWeight: 700,
                cursor: 'pointer',
                letterSpacing: '0.04em',
              }}
            >
              New Session
            </button>
          </div>
        </div>

        {/* Right: prominent customer display */}
        {customerProfile && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0, background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-light)', borderRadius: 10, padding: '6px 12px 6px 6px' }}>
            <div style={{ width: 64, height: 64, borderRadius: 8, overflow: 'hidden', flexShrink: 0, background: 'var(--bg-surface)', border: '2px solid var(--border-light)' }}>
              <video
                autoPlay muted loop playsInline
                src={
                  customerProfile === 'female' ? '/demo-videos/Female-VTO.mp4'
                  : customerProfile === 'male' ? '/demo-videos/Male-VTO.mp4'
                  : customerProfile === 'female-senior' ? '/demo-videos/Female-Senior-VTO.mp4'
                  : '/demo-videos/Male-Senior-VTO.mp4'
                }
                style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center 12%', display: 'block' }}
              />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.10em', textTransform: 'uppercase', color: 'var(--text-dim)' }}>Customer</span>
              <span style={{ fontSize: 14, fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '0.02em' }}>
                {
                  customerProfile === 'female' ? 'Female Customer'
                  : customerProfile === 'male' ? 'Male Customer'
                  : customerProfile === 'female-senior' ? 'Female Senior Customer'
                  : 'Male Senior Customer'
                }
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Messages + floating controls */}
      <div style={{ flex: 1, position: 'relative', minHeight: 0 }}>
        {/* Scrollable messages */}
        <div style={{ position: 'absolute', inset: 0, overflowY: 'auto', padding: '16px', paddingBottom: customerProfile ? (showTextInput ? 98 : 88) : 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {!customerProfile ? (
            <CustomerSelector onSelect={handleCustomerProfileChange} />
          ) : (
            <>
              {isTyping && messages.length === 0 && <SkeletonMessage />}
              {messages.map(msg => (
                <MessageBubble
                  key={msg.id}
                  message={msg}
                  isLatest={msg.id === lastAgentMsgId}
                  showFeedback={msg.id === feedbackMessageId}
                  feedbackGarment={msg.id === feedbackMessageId ? (completedPipeline?.garment ?? null) : null}
                  sessionId={sessionId}
                />
              ))}
              {isTyping && messages.length > 0 && <TypingIndicator agent={typingAgent} />}
              <div ref={bottomRef} />
            </>
          )}
        </div>

        {customerProfile && <>
        {/* Chat composer */}
        {showTextInput && !micActive && (
          <div style={{
            position: 'absolute', bottom: 12, right: 12, left: 12,
            animation: 'fadeIn 0.15s ease',
          }}>
            <div style={{
              position: 'relative',
              background: 'var(--bg-elevated)',
              borderRadius: 14,
              boxShadow: '0 14px 38px rgba(0,0,0,0.45)',
            }}>
              <button
                onClick={handleMicToggle}
                disabled={!canUseInput}
                title={
                  !customerProfile ? 'Select the store customer first' :
                  MIC_BLOCKED_INSECURE_CONTEXT ? 'Voice input needs HTTPS or localhost' :
                  'Speak to the agent'
                }
                style={{
                  position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)',
                  background: 'none', border: 'none', padding: 4,
                  color: canUseInput ? 'var(--text-secondary)' : 'var(--text-dim)',
                  cursor: canUseInput ? 'pointer' : 'not-allowed',
                  display: 'flex', alignItems: 'center',
                }}
              >
                <Mic size={16} />
              </button>
              <input
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') { void handleSend() } if (e.key === 'Escape') setShowTextInput(false) }}
                disabled={!canUseInput}
                placeholder={customerProfile ? 'Type a message...' : 'Select the store customer first'}
                style={{
                  width: '100%', boxSizing: 'border-box',
                  background: 'transparent', border: 'none',
                  borderRadius: 14, padding: '10px 40px 10px 36px', fontSize: 14,
                  color: 'var(--text-primary)', opacity: canUseInput ? 1 : 0.55, outline: 'none', fontFamily: 'var(--font)',
                }}
              />
              <button
                onClick={() => { void handleSend() }}
                disabled={!input.trim() || !canUseInput}
                style={{
                  position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)',
                  background: 'none', border: 'none', padding: 4,
                  color: input.trim() && canUseInput ? 'var(--teal)' : 'var(--text-dim)',
                  cursor: input.trim() && canUseInput ? 'pointer' : 'default',
                  display: 'flex', alignItems: 'center',
                }}
              >
                <Send size={16} />
              </button>
            </div>
          </div>
        )}

        {/* Voice-first input */}
        {!showTextInput && (
          <button
            onClick={handleMicToggle}
            disabled={!canUseInput}
            title={
              !customerProfile ? 'Select the store customer first' :
              MIC_BLOCKED_INSECURE_CONTEXT ? 'Voice input needs HTTPS or localhost' :
              micActive ? 'Stop listening' : 'Speak to the agent'
            }
            style={{
              position: 'absolute',
              bottom: 16,
              left: '50%',
              transform: 'translateX(-50%)',
              width: 58,
              height: 58,
              borderRadius: '50%',
              border: 'none',
              cursor: canUseInput ? 'pointer' : 'not-allowed',
              background: !canUseInput ? 'var(--elevated)' : micActive ? 'var(--red-soft)' : 'var(--gradient-primary)',
              opacity: canUseInput ? 1 : 0.55,
              boxShadow: micActive
                ? '0 0 0 6px rgba(237,28,36,0.18), 0 0 24px rgba(237,28,36,0.38)'
                : '0 8px 24px rgba(0,0,0,0.5)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              zIndex: 10,
            }}
          >
            {micActive ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                {[0.7, 1.2, 0.8, 1.4, 0.6].map((h, i) => (
                  <div key={i} style={{
                    width: 3,
                    borderRadius: 2,
                    background: 'var(--red)',
                    height: `${h * 12}px`,
                    animation: `typing ${0.7 + i * 0.1}s ${i * 0.07}s ease-in-out infinite alternate`,
                  }} />
                ))}
              </div>
            ) : (
              <svg width="23" height="23" viewBox="0 0 24 24" fill="none"
                stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="2" width="6" height="11" rx="3" />
                <path d="M5 10a7 7 0 0 0 14 0" />
                <line x1="12" y1="19" x2="12" y2="22" />
                <line x1="8" y1="22" x2="16" y2="22" />
              </svg>
            )}
          </button>
        )}

        <button
          onClick={() => {
            if (micActive) stopRecognition()
            setShowTextInput(v => !v)
          }}
          title={showTextInput ? 'Hide text input' : 'Type a message'}
          style={{
            position: 'absolute',
            bottom: showTextInput ? 82 : 28,
            right: 20,
            width: 34,
            height: 34,
            borderRadius: '50%',
            border: `1px solid ${showTextInput ? 'var(--teal)' : 'var(--border)'}`,
            background: showTextInput ? 'rgba(0,124,151,0.12)' : 'rgba(30,30,38,0.9)',
            color: showTextInput ? 'var(--teal)' : 'var(--text-muted)',
            display: 'grid',
            placeItems: 'center',
            boxShadow: '0 3px 12px rgba(0,0,0,0.35)',
            zIndex: 11,
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <rect x="2" y="4" width="20" height="16" rx="2" />
            <line x1="8" y1="12" x2="16" y2="12" />
            <line x1="8" y1="16" x2="12" y2="16" />
          </svg>
        </button>
        </>}

      </div>
    </div>
  )
}

function AgentAvatar({
  agentKey,
  config,
  isThinking = false,
  size = 62,
}: {
  agentKey?: string | null
  config?: AgentConfig | null
  isThinking?: boolean
  size?: number
}) {
  const color = config?.color ?? '#ed1c24'
  const initials = config?.initials ?? 'OC'
  const filename = config?.avatar ?? agentKey
  const [imgOk, setImgOk] = useState(!!filename)

  useEffect(() => { setImgOk(!!filename) }, [filename])

  const avatarSrc = filename ? `/assets/avatars/${filename}.png?v=${AVATAR_V}` : null
  const showImg = !!(avatarSrc && imgOk && filename)

  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        flexShrink: 0,
        background: showImg ? 'transparent' : `${color}18`,
        border: showImg ? 'none' : `1.5px solid ${color}44`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        overflow: 'hidden',
        boxShadow: isThinking
          ? `0 0 22px ${color}80, 0 0 8px ${color}45`
          : `0 0 12px ${color}40`,
        animation: isThinking ? 'agentThinking 1.4s ease-in-out infinite' : undefined,
      }}
    >
      {showImg ? (
        <img
          alt=""
          src={avatarSrc!}
          onError={() => setImgOk(false)}
          style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top center', display: 'block' }}
        />
      ) : (
        <span style={{ fontSize: size * 0.36, color: 'rgba(255,255,255,0.95)', fontWeight: 800, letterSpacing: '-0.02em' }}>
          {initials}
        </span>
      )}
    </div>
  )
}

function MessageBubble({
  message,
  isLatest = false,
  showFeedback = false,
  feedbackGarment = null,
  sessionId,
}: {
  message: Message
  isLatest?: boolean
  showFeedback?: boolean
  feedbackGarment?: Garment | null
  sessionId?: string
}) {
  const isAgent = message.role === 'agent'
  const cfg = message.agent ? AGENT_CONFIG[message.agent] : null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: isAgent ? 'flex-start' : 'flex-end', animation: isAgent ? 'agentPopOut 0.24s ease both' : 'fadeIn 0.2s ease' }}>
      <div style={{ display: 'flex', flexDirection: isAgent ? 'row' : 'row-reverse', gap: 12, alignItems: 'flex-start', maxWidth: '92%' }}>
        {isAgent && <AgentAvatar agentKey={message.agent} config={cfg} />}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: isAgent ? 'flex-start' : 'flex-end', gap: 5, minWidth: 0 }}>
          {isAgent && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0 2px' }}>
              <span style={{ fontSize: 12, color: cfg?.color ?? 'var(--text-dim)', fontWeight: 900 }}>{cfg?.label ?? 'OpenClaw'}</span>
            </div>
          )}
          <div style={{
            padding: '12px 14px',
            borderRadius: isAgent ? '6px 16px 16px 16px' : '16px 6px 16px 16px',
            background: isAgent
              ? 'linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.02))'
              : 'linear-gradient(135deg, rgba(237,28,36,0.18), rgba(242,101,34,0.10))',
            border: `1px solid ${isAgent ? (cfg ? `${cfg.color}36` : 'var(--border)') : 'rgba(237,28,36,0.34)'}`,
            boxShadow: isAgent && cfg ? `0 14px 32px ${cfg.color}12` : '0 12px 28px rgba(237,28,36,0.08)',
            fontFamily: 'var(--font)',
            fontSize: 15,
            color: isAgent ? 'rgba(255,255,255,0.82)' : 'rgba(255,255,255,0.97)',
            lineHeight: 1.55,
          }}>
            {isAgent
              ? <TypewriterText text={message.content} isLatest={isLatest} />
              : <MarkdownContent text={message.content} />
            }
          </div>
          <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.28)' }}>{message.timestamp}</span>
        </div>
      </div>
      {showFeedback && feedbackGarment && sessionId && (
        <div style={{ paddingLeft: 60, marginTop: 6, maxWidth: '92%', width: '100%' }}>
          <InlineFeedback garment={feedbackGarment} sessionId={sessionId} />
        </div>
      )}
    </div>
  )
}

function InlineFeedback({ garment, sessionId }: { garment: Garment; sessionId: string }) {
  const [hovered, setHovered] = useState(0)
  const [selected, setSelected] = useState(0)
  const [submitted, setSubmitted] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [note, setNote] = useState('')
  const [showNote, setShowNote] = useState(false)
  const cfgColor = AGENT_CONFIG.CustomerFeedbackAgent.color

  async function submit() {
    if (submitting || submitted || selected === 0) return
    setSubmitting(true)
    try {
      const tid = safeRandomUUID()
      await fetch(`/api/v1/vto/history/${tid}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          garment_id: garment.id,
          shopping_experience_rating: selected,
          feedback: note.trim() || undefined,
        }),
      })
    } catch { /* best-effort */ }
    setSubmitting(false)
    setSubmitted(true)
  }

  if (submitted) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '10px 14px', borderRadius: '8px 16px 16px 16px',
        background: 'rgba(28,28,34,0.95)',
        border: '1px solid rgba(255,255,255,0.1)',
        animation: 'fadeIn 0.2s ease',
      }}>
        <span style={{ fontSize: 16, color: '#f5c518', letterSpacing: 2 }}>
          {'★'.repeat(selected)}<span style={{ color: 'rgba(255,255,255,0.18)' }}>{'★'.repeat(5 - selected)}</span>
        </span>
        <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.82)', fontWeight: 600 }}>
          Thanks for your feedback!
        </span>
      </div>
    )
  }

  const activeStars = hovered || selected

  return (
    <div style={{
      padding: '12px 14px', borderRadius: '8px 16px 16px 16px',
      background: 'rgba(28,28,34,0.95)',
      border: '1px solid rgba(255,255,255,0.1)',
      animation: 'agentPopOut 0.24s ease both',
    }}>
      <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.7)', marginBottom: 8, fontWeight: 600 }}>
        Rate your try-on experience
      </div>
      <div style={{ display: 'flex', gap: 5, marginBottom: showNote ? 8 : 0 }}>
        {[1, 2, 3, 4, 5].map(star => (
          <button
            key={star}
            onMouseEnter={() => setHovered(star)}
            onMouseLeave={() => setHovered(0)}
            onClick={() => {
              setSelected(star)
              setShowNote(true)
            }}
            style={{
              background: 'none', border: 'none', padding: 0,
              cursor: 'pointer', fontSize: 26, lineHeight: 1,
              color: star <= activeStars ? '#f5c518' : 'rgba(255,255,255,0.3)',
              transform: star <= activeStars ? 'scale(1.15)' : 'scale(1)',
              transition: 'color 0.12s, transform 0.12s',
              textShadow: star <= activeStars ? '0 0 8px rgba(245,197,24,0.5)' : 'none',
            }}
          >
            ★
          </button>
        ))}
      </div>
      {showNote && (
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6, animation: 'fadeIn 0.15s ease' }}>
          <input
            value={note}
            onChange={e => setNote(e.target.value)}
            placeholder="Any comments? (optional)"
            style={{
              background: 'rgba(20,20,26,0.9)', border: '1px solid rgba(255,255,255,0.12)',
              borderRadius: 8, color: '#fff',
              fontSize: 12, padding: '7px 10px',
              fontFamily: 'var(--font)', outline: 'none', width: '100%', boxSizing: 'border-box',
            }}
          />
          <button
            onClick={() => { void submit() }}
            disabled={submitting}
            style={{
              alignSelf: 'flex-start', padding: '6px 16px', borderRadius: 8,
              border: 'none',
              background: submitting ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.15)',
              color: '#fff', fontSize: 12, fontWeight: 700,
              cursor: submitting ? 'not-allowed' : 'pointer',
            }}
          >
            {submitting ? 'Submitting…' : 'Submit'}
          </button>
        </div>
      )}
    </div>
  )
}

function SkeletonSlab({ width = '100%', height = 12, radius = 4 }: { width?: number | string; height?: number; radius?: number }) {
  return (
    <div style={{
      width, height, borderRadius: radius, flexShrink: 0,
      background: 'linear-gradient(90deg, #1a1a1e 25%, #252528 50%, #1a1a1e 75%)',
      backgroundSize: '200% 100%',
      animation: 'shimmer 1.5s ease-in-out infinite',
    }} />
  )
}

function SkeletonMessage() {
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', animation: 'fadeIn 0.2s ease' }}>
      <div style={{ width: 48, height: 48, borderRadius: '50%', flexShrink: 0, background: 'linear-gradient(90deg, #1a1a1e 25%, #252528 50%, #1a1a1e 75%)', backgroundSize: '200% 100%', animation: 'shimmer 1.5s ease-in-out infinite' }} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: '70%' }}>
        <SkeletonSlab width={80} height={10} />
        <div style={{ padding: '12px 14px', borderRadius: '6px 16px 16px 16px', background: 'var(--bg-elevated)', border: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <SkeletonSlab width="92%" height={11} />
          <SkeletonSlab width="78%" height={11} />
          <SkeletonSlab width="60%" height={11} />
        </div>
      </div>
    </div>
  )
}

function TypingIndicator({ agent }: { agent: string }) {
  const cfg = AGENT_CONFIG[agent]
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14 }}>
      {cfg && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <AgentAvatar agentKey={agent} config={cfg} isThinking />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div>
              <span style={{ fontSize: 13, color: cfg.color, fontWeight: 900 }}>{cfg.label}</span>
            </div>
            <div style={{ padding: '10px 14px', background: 'var(--bg-elevated)', border: `1px solid ${cfg.color}30`, borderRadius: '6px 18px 18px 18px', width: 'fit-content' }}>
              <span className="shiny-text" style={{ fontSize: 13 }}>thinking…</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ //
// Customer Selector Toggle
// ------------------------------------------------------------------ //

const SELECTOR_OPTIONS: { value: CustomerProfile; label: string; videoSrc: string }[] = [
  { value: 'female',        label: 'Female Customer',        videoSrc: '/demo-videos/Female-VTO.mp4'        },
  { value: 'male',          label: 'Male Customer',          videoSrc: '/demo-videos/Male-VTO.mp4'          },
  { value: 'female-senior', label: 'Female Senior Customer', videoSrc: '/demo-videos/Female-Senior-VTO.mp4' },
  { value: 'male-senior',   label: 'Male Senior Customer',   videoSrc: '/demo-videos/Male-Senior-VTO.mp4'   },
]

function CustomerSelector({ onSelect }: { onSelect: (v: CustomerProfile) => void }) {
  const [hovered, setHovered] = useState<CustomerProfile | ''>('')

  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      gap: 24, padding: '24px 16px', minHeight: '100%',
    }}>
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--text-primary)', marginBottom: 6 }}>
          Select Customer
        </div>
        <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>
          Choose a profile to start your session
        </div>
      </div>

      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', justifyContent: 'center' }}>
        {SELECTOR_OPTIONS.map(opt => {
          const isHovered = hovered === opt.value
          return (
            <button
              key={opt.value}
              onClick={() => onSelect(opt.value)}
              onMouseEnter={() => setHovered(opt.value)}
              onMouseLeave={() => setHovered('')}
              style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                gap: 10, padding: '12px 12px 14px',
                background: isHovered
                  ? 'linear-gradient(135deg, rgba(237,28,36,0.10), rgba(242,101,34,0.07))'
                  : 'var(--bg-elevated)',
                border: `1.5px solid ${isHovered ? 'rgba(242,101,34,0.55)' : 'var(--border)'}`,
                borderRadius: 16, cursor: 'pointer', width: 'clamp(88px, 30%, 126px)',
                transition: 'all 0.15s ease',
                boxShadow: isHovered ? '0 6px 22px rgba(242,101,34,0.14)' : 'none',
              }}
            >
              <div style={{
                width: '100%', aspectRatio: '90 / 116', borderRadius: 10,
                overflow: 'hidden', background: 'var(--bg-surface)',
                border: `1px solid ${isHovered ? 'rgba(242,101,34,0.3)' : 'var(--border)'}`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                transition: 'border-color 0.15s ease',
              }}>
                <video
                  autoPlay muted loop playsInline
                  src={opt.videoSrc}
                  style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top center', display: 'block' }}
                />
              </div>
              <span style={{
                fontSize: 11, fontWeight: 700, letterSpacing: '0.05em',
                textTransform: 'uppercase', textAlign: 'center',
                color: isHovered ? 'var(--text-primary)' : 'var(--text-secondary)',
                transition: 'color 0.15s',
              }}>
                {opt.label}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
