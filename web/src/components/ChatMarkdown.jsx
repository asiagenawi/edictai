import ReactMarkdown from 'react-markdown'

function ChatMarkdown({ content }) {
  return (
    <div className="chat-markdown">
      <ReactMarkdown>{content}</ReactMarkdown>
    </div>
  )
}

export default ChatMarkdown
