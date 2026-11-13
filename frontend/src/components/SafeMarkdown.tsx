import React from 'react'
import ReactMarkdown from 'react-markdown'

interface SafeMarkdownProps {
  content: string
  className?: string
}

/**
 * Component render Markdown an toàn tuyệt đối, tuân thủ FR-QUIZ-09:
 * - Không bật HTML thô (không dùng rehype-raw, không dangerouslySetInnerHTML).
 * - Chặn javascript: URLs.
 * - Chỉ cho phép định dạng an toàn (đậm, nghiêng, code, list, blockquote, link an toàn).
 * - Các thẻ nguy hiểm như <script>, <img onerror>, <iframe> hiển thị dưới dạng văn bản thường hoặc bị loại bỏ.
 */
export const SafeMarkdown: React.FC<SafeMarkdownProps> = ({ content, className = '' }) => {
  return (
    <div className={`prose prose-invert max-w-none text-slate-200 leading-relaxed break-words ${className}`}>
      <ReactMarkdown
        allowedElements={[
          'p',
          'span',
          'strong',
          'b',
          'em',
          'i',
          'code',
          'pre',
          'ul',
          'ol',
          'li',
          'blockquote',
          'h1',
          'h2',
          'h3',
          'h4',
          'a',
          'hr',
        ]}
        urlTransform={(url) => {
          if (/^(?:javascript|vbscript):/i.test(url)) {
            return ''
          }
          return url
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
