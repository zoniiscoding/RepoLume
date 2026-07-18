import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";

export function MarkdownAnswer({ children }: { children: string }): React.JSX.Element {
  return (
    <div className="markdown-answer">
      <ReactMarkdown
        rehypePlugins={[rehypeSanitize]}
        components={{
          a: ({ children: linkChildren }) => (
            <span className="markdown-answer__link-text">{linkChildren}</span>
          ),
          code: ({ className, children: codeChildren, ...props }) => (
            <code className={className} {...props}>
              {codeChildren}
            </code>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
