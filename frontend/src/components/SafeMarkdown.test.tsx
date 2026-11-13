import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SafeMarkdown } from './SafeMarkdown'

describe('SafeMarkdown Component (FR-QUIZ-09 - Chống XSS)', () => {
  it('renders standard markdown formatting safely', () => {
    const content = '# Tiêu đề\n\nĐây là văn bản **in đậm**, *in nghiêng* và `code block`.'
    render(<SafeMarkdown content={content} />)

    expect(screen.getByText('Tiêu đề')).toBeInTheDocument()
    expect(screen.getByText('in đậm')).toBeInTheDocument()
    expect(screen.getByText('in nghiêng')).toBeInTheDocument()
    expect(screen.getByText('code block')).toBeInTheDocument()
  })

  it('neutralizes <script> tags without executing them or rendering HTML script elements', () => {
    const malicious = '<script>window.__xss_executed__ = true;</script>Nội dung hợp lệ'
    const { container } = render(<SafeMarkdown content={malicious} />)

    // Verify no <script> tag is present in the DOM
    const scriptTag = container.querySelector('script')
    expect(scriptTag).toBeNull()

    // Global variable was not set
    expect((window as unknown as { __xss_executed__?: boolean }).__xss_executed__).toBeUndefined()

    // Valid text is preserved
    expect(screen.getByText(/Nội dung hợp lệ/)).toBeInTheDocument()
  })

  it('neutralizes <img onerror=...> injection without rendering img or triggering error handler', () => {
    const malicious = '<img src="invalid-url" onerror="window.__xss_img__ = true;" />Hình ảnh kiểm tra'
    const { container } = render(<SafeMarkdown content={malicious} />)

    // Image tag with onerror must not be rendered because raw HTML is not parsed
    const imgTag = container.querySelector('img')
    expect(imgTag).toBeNull()

    // Global variable was not set
    expect((window as unknown as { __xss_img__?: boolean }).__xss_img__).toBeUndefined()
    expect(screen.getByText(/Hình ảnh kiểm tra/)).toBeInTheDocument()
  })

  it('blocks javascript: scheme in markdown links', () => {
    const malicious = '[Bấm vào đây](javascript:alert("hacked"))'
    const { container } = render(<SafeMarkdown content={malicious} />)

    const link = container.querySelector('a')
    expect(link).toBeInTheDocument()
    // href should be empty or blocked by urlTransform
    expect(link?.getAttribute('href')).toBe('')
  })
})
