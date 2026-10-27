import { useCallback, useEffect, useRef, useState } from 'react'

// Minimal typing for the Web Speech API (not in every TS DOM lib, and prefixed in Chrome).
interface RecognitionAlternative {
  transcript: string
}
interface RecognitionResult {
  isFinal: boolean
  0: RecognitionAlternative
}
interface RecognitionEvent {
  resultIndex: number
  results: ArrayLike<RecognitionResult>
}
interface Recognition {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((e: RecognitionEvent) => void) | null
  onend: (() => void) | null
  onerror: ((e: { error: string }) => void) | null
  start(): void
  stop(): void
  abort(): void
}
type RecognitionCtor = new () => Recognition

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor
    webkitSpeechRecognition?: RecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export const voiceSupported = (): boolean =>
  recognitionCtor() !== null && typeof window.speechSynthesis !== 'undefined'

function pickVoice(): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis.getVoices()
  const en = voices.filter((v) => v.lang === 'en-US' || v.lang.startsWith('en-US'))
  return (
    en.find((v) => /natural|premium|enhanced|samantha|google us english/i.test(v.name)) ??
    en[0] ??
    voices.find((v) => v.lang.startsWith('en')) ??
    null
  )
}

/**
 * Half-duplex voice loop: listen → send final transcript → speak reply → listen again.
 * Recognition is stopped while the agent speaks so it doesn't transcribe itself.
 */
export function useVoice(onFinal: (text: string) => void) {
  const [listening, setListening] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [interim, setInterim] = useState('')
  const [error, setError] = useState<string | null>(null)
  const recRef = useRef<Recognition | null>(null)
  const wantListening = useRef(false)
  const onFinalRef = useRef(onFinal)
  onFinalRef.current = onFinal

  const startListening = useCallback(() => {
    const Ctor = recognitionCtor()
    if (!Ctor) return
    wantListening.current = true
    if (recRef.current) return
    const rec = new Ctor()
    rec.lang = 'en-US'
    rec.continuous = false
    rec.interimResults = true
    rec.onresult = (e) => {
      let partial = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i]
        if (r.isFinal) {
          const text = r[0].transcript.trim()
          setInterim('')
          if (text) {
            // A turn is now in flight; don't auto-restart until the reply has been spoken.
            wantListening.current = false
            onFinalRef.current(text)
          }
        } else {
          partial += r[0].transcript
        }
      }
      if (partial) setInterim(partial)
    }
    rec.onerror = (e) => {
      if (e.error !== 'no-speech' && e.error !== 'aborted') setError(`microphone: ${e.error}`)
    }
    rec.onend = () => {
      recRef.current = null
      setListening(false)
      if (wantListening.current) {
        // Recognition times out on silence; keep the line open.
        setTimeout(() => wantListening.current && startListening(), 150)
      }
    }
    recRef.current = rec
    try {
      rec.start()
      setListening(true)
      setError(null)
    } catch (err) {
      recRef.current = null
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  const stopListening = useCallback(() => {
    wantListening.current = false
    recRef.current?.abort()
    recRef.current = null
    setListening(false)
    setInterim('')
  }, [])

  const speak = useCallback(
    (text: string, resumeAfter: boolean) => {
      stopListening()
      const synth = window.speechSynthesis
      synth.cancel()
      const u = new SpeechSynthesisUtterance(text)
      const voice = pickVoice()
      if (voice) u.voice = voice
      u.lang = 'en-US'
      u.rate = 1.03
      u.onstart = () => setSpeaking(true)
      const done = () => {
        setSpeaking(false)
        if (resumeAfter) startListening()
      }
      u.onend = done
      u.onerror = done
      synth.speak(u)
    },
    [startListening, stopListening],
  )

  const stopAll = useCallback(() => {
    stopListening()
    window.speechSynthesis?.cancel()
    setSpeaking(false)
  }, [stopListening])

  useEffect(() => {
    // Voices load asynchronously in Chrome.
    window.speechSynthesis?.getVoices()
    return () => {
      wantListening.current = false
      recRef.current?.abort()
      window.speechSynthesis?.cancel()
    }
  }, [])

  return { listening, speaking, interim, error, startListening, stopListening, speak, stopAll }
}
