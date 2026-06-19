import { useEffect, useRef, useState } from 'react'
import './App.css'

const API_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'
const POSTER_BASE_URL = 'https://image.tmdb.org/t/p/w300'

let nextMessageId = 0
const makeMessageId = () => nextMessageId++

function Poster({ movie, className }) {
  return movie.poster_path ? (
    <img src={`${POSTER_BASE_URL}${movie.poster_path}`} alt={movie.title} />
  ) : (
    <span className={className}>POSTER ART</span>
  )
}

function FeatureCard({ movie }) {
  const year = movie.release_date ? movie.release_date.slice(0, 4) : null
  const matchPct = Math.round(movie.similarity * 100)

  return (
    <div className="cm-feature-card">
      <div className="cm-feature-strip">
        <span className="cm-strip-tag">★ TOP PICK</span>
        <span className="cm-strip-tag">MATCH {matchPct}%</span>
      </div>
      <div className="cm-feature-body">
        <div className="cm-feature-poster">
          <Poster movie={movie} className="cm-poster-label" />
          {movie.vote_average != null && (
            <div className="cm-feature-rating">★ {movie.vote_average.toFixed(1)} RATING</div>
          )}
        </div>
        <div className="cm-feature-info">
          <div className="cm-feature-title-row">
            <span className="cm-feature-title">{movie.title}</span>
            {year && <span className="cm-feature-year">{year}</span>}
          </div>
          <div className="cm-feature-genres">
            {movie.genres.map((genre) => (
              <span key={genre} className="cm-genre-pill">
                {genre.toUpperCase()}
              </span>
            ))}
          </div>
          {movie.overview && <div className="cm-feature-overview">{movie.overview}</div>}
          <div className="cm-feature-footer">
            <span className="cm-feature-match">FOUND IN THE STACKS</span>
            <a
              className="cm-feature-link"
              href={`https://www.themoviedb.org/movie/${movie.tmdb_id}`}
              target="_blank"
              rel="noreferrer"
            >
              VIEW ▸
            </a>
          </div>
        </div>
      </div>
    </div>
  )
}

function TapeCard({ movie }) {
  const year = movie.release_date ? movie.release_date.slice(0, 4) : null

  return (
    <div className="cm-tape-card">
      <div className="cm-tape-poster">
        <Poster movie={movie} className="cm-poster-label" />
      </div>
      <div className="cm-tape-info">
        <div className="cm-tape-title-row">
          <span className="cm-tape-title">{movie.title}</span>
          {year && <span className="cm-tape-year">{year}</span>}
        </div>
        <div className="cm-tape-genres">
          {movie.genres.slice(0, 2).map((genre) => (
            <span key={genre} className="cm-genre-pill">
              {genre.toUpperCase()}
            </span>
          ))}
        </div>
        {movie.overview && <div className="cm-tape-overview">{movie.overview}</div>}
        <div className="cm-tape-footer">
          <span>{movie.vote_average != null ? `★ ${movie.vote_average.toFixed(1)}` : ''}</span>
          <span>MATCH {Math.round(movie.similarity * 100)}%</span>
        </div>
      </div>
    </div>
  )
}

function Turn({ message }) {
  if (message.role === 'user') {
    return <div className="cm-user-line">&gt; {message.content}</div>
  }

  if (message.role === 'error') {
    return <div className="cm-error-turn">⚠ {message.content}</div>
  }

  const [hero, ...rest] = message.recommendations ?? []

  return (
    <div className="cm-turn">
      <div className="cm-reply">{message.content}</div>
      {hero && <FeatureCard movie={hero} />}
      {rest.length > 0 && (
        <>
          <div className="cm-more-label">◄◄ MORE IN THIS VEIN</div>
          <div className="cm-tape-grid">
            {rest.map((movie) => (
              <TapeCard key={movie.id} movie={movie} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function App() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const bottomRef = useRef(null)
  const hasMessages = messages.length > 0

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isLoading])

  async function sendMessage(event) {
    event.preventDefault()
    const text = input.trim()
    if (!text || isLoading) return

    const history = messages
      .filter((message) => message.role !== 'error')
      .map(({ role, content }) => ({ role, content }))
    setMessages((prev) => [...prev, { id: makeMessageId(), role: 'user', content: text }])
    setInput('')
    setIsLoading(true)

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, history }),
      })
      if (!response.ok) {
        throw new Error(
          `The backend returned an error (${response.status}) — it may be rate-limited, try again shortly.`
        )
      }
      const data = await response.json()
      setMessages((prev) => [
        ...prev,
        {
          id: makeMessageId(),
          role: 'assistant',
          content: data.reply,
          recommendations: data.recommendations,
        },
      ])
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { id: makeMessageId(), role: 'error', content: err.message || 'Could not reach the backend.' },
      ])
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="cm-app">
      <div className="cm-glow cm-glow--pink" />
      <div className="cm-glow cm-glow--cyan" />
      <div className="cm-vignette" />
      <div className="cm-scanlines" />
      <div className="cm-scan-sweep" />
      <div className="cm-grain" />

      <header className="cm-hud">
        <div className="cm-hud-left">
          <div className="cm-logo">
            CINE<span className="cm-logo-accent">BUFF</span>
          </div>
          <div className="cm-transport">
            <span className="cm-transport-play">▶ PLAY</span>
            <span className="cm-transport-sp">SP</span>
            <span className="cm-transport-ff">►►</span>
          </div>
        </div>
        <div className="cm-hud-right">
          <span className="cm-rec-dot" />
          <span className="cm-status">ONLINE</span>
        </div>
      </header>

      <div className={`cm-main${hasMessages ? '' : ' cm-main--empty'}`}>
        <div className="cm-stage">
          <div className="cm-stage-inner">
            {messages.map((message) => (
              <Turn key={message.id} message={message} />
            ))}
            {isLoading && (
              <div className="cm-loading">
                <span className="cm-loading-cursor" />
                DIGGING THROUGH THE STACKS…
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        <form className="cm-terminal" onSubmit={sendMessage}>
          <div className="cm-terminal-bar">
            <span className="cm-terminal-prompt">&gt;</span>
            <input
              type="text"
              className="cm-terminal-input"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="tell me what you're in the mood for_"
              disabled={isLoading}
            />
            <span className="cm-terminal-cursor" />
            <button type="submit" className="cm-terminal-send" disabled={isLoading || !input.trim()}>
              SEND ▸
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default App
