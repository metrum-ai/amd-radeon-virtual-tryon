// Copyright Advanced Micro Devices, Inc.
// 
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react'

const AGENT_SPEECH_URL = '/api/v1/vto/agent/speech'
const MAX_SPEECH_CHARS = 260
const SPEECH_DECORATIVE_SYMBOLS = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\uFE0F]/gu

function speechText(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, '')
    .replace(SPEECH_DECORATIVE_SYMBOLS, '')
    .replace(/[*_`#>-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function speechChunks(text: string): string[] {
  const sentences = text.match(/[^.!?]+[.!?]*/g) ?? [text]
  const chunks: string[] = []
  let current = ''

  for (const sentence of sentences.map(item => item.trim()).filter(Boolean)) {
    const next = current ? `${current} ${sentence}` : sentence
    if (next.length <= MAX_SPEECH_CHARS) {
      current = next
      continue
    }
    if (current) chunks.push(current)
    current = sentence
  }

  if (current) chunks.push(current)
  return chunks.length ? chunks : [text]
}

export function useAgentSpeech() {
  const [isMuted, setIsMuted] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const urlRef = useRef<string | null>(null)

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    audioRef.current?.pause()
    audioRef.current = null
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current)
      urlRef.current = null
    }
    setIsSpeaking(false)
  }, [])

  const speak = useCallback(async (text: string) => {
    const input = speechText(text)
    if (isMuted || !input) return

    stop()
    const controller = new AbortController()
    abortRef.current = controller

    const requestSpeech = async (chunk: string): Promise<Blob | null> => {
      const response = await fetch(AGENT_SPEECH_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          input: chunk,
          voice: 'af_bella',
          language: 'English',
          speed: 1.25,
        }),
        signal: controller.signal,
      })
      if (!response.ok) return null
      return response.blob()
    }

    const playBlob = async (blob: Blob): Promise<void> => {
      const url = URL.createObjectURL(blob)
      urlRef.current = url

      const audio = new Audio(url)
      audioRef.current = audio
      setIsSpeaking(true)

      await new Promise<void>((resolve, reject) => {
        audio.onended = () => resolve()
        audio.onerror = () => reject(new Error('Speech playback failed'))
        void audio.play().catch(reject)
      })

      if (urlRef.current === url) {
        URL.revokeObjectURL(url)
        urlRef.current = null
      }
    }

    try {
      const chunks = speechChunks(input)
      let nextBlob = requestSpeech(chunks[0])

      for (let i = 0; i < chunks.length; i++) {
        const blob = await nextBlob
        if (!blob) break
        nextBlob = i + 1 < chunks.length
          ? requestSpeech(chunks[i + 1])
          : Promise.resolve(null)
        await playBlob(blob)
      }

      setIsSpeaking(false)
    } catch (error) {
      if ((error as DOMException).name !== 'AbortError') stop()
    }
  }, [isMuted, stop])

  useEffect(() => stop, [stop])

  return { isMuted, isSpeaking, setIsMuted, speak, stop }
}
