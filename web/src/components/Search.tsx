import { useEffect, useRef, useState } from 'react'
import { api } from '../client'
import type { TokenLabel } from '../types'

interface Props {
  onPick: (address: string) => void
  placeholder?: string
}

/** Find a coin by symbol, name or address; picking one hands the address to the current view. */
export default function Search({ onPick, placeholder = 'find a coin: symbol, name, address' }: Props) {
  const [text, setText] = useState('')
  const [hits, setHits] = useState<TokenLabel[]>([])
  const [open, setOpen] = useState(false)
  const timer = useRef<number | null>(null)
  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      if (text.trim().length < 2) {
        setHits([])
        return
      }
      api
        .search(text)
        .then((h) => {
          setHits(h)
          setOpen(true)
        })
        .catch(() => setHits([]))
    }, 180)
  }, [text])
  return (
    <div className="search">
      <input
        type="text"
        placeholder={placeholder}
        value={text}
        aria-label="Find a coin"
        onChange={(e) => setText(e.target.value)}
        onFocus={() => hits.length && setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && hits[0]) {
            onPick(hits[0].address)
            setOpen(false)
            setText('')
          } else if (e.key === 'Escape') {
            setOpen(false)
            ;(e.target as HTMLInputElement).blur()
          }
        }}
      />
      {open && hits.length > 0 && (
        <ul role="listbox">
          {hits.map((h) => (
            <li
              key={h.address}
              role="option"
              aria-selected={false}
              onMouseDown={() => {
                onPick(h.address)
                setOpen(false)
                setText('')
              }}
            >
              <span>
                <b>{h.symbol}</b> <span className="muted">{h.name}</span>
              </span>
              <span className="faint">{h.short}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
