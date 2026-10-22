"use client";

import { memo } from "react";
import { CheckIcon, CopyIcon } from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { useCopy } from "@/hooks/use-copy";

function CodeBlock({ language, code }: { language: string; code: string }) {
  const { copied, copy } = useCopy();

  return (
    <div className="not-prose my-4 overflow-hidden rounded-lg border bg-muted/40">
      <div className="flex items-center justify-between border-b px-3 py-1">
        <span className="font-mono text-xs text-muted-foreground">{language || "text"}</span>
        <Button variant="ghost" size="xs" onClick={() => copy(code)}>
          {copied ? <CheckIcon /> : <CopyIcon />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre className="overflow-x-auto p-3 font-mono text-[13px] leading-relaxed">
        <code>{code}</code>
      </pre>
    </div>
  );
}

const components: Components = {
  // Fenced blocks render through CodeBlock; `pre` just passes its child through.
  pre: ({ children }) => <>{children}</>,
  code: ({ className, children }) => {
    const language = /language-(\w+)/.exec(className ?? "")?.[1];
    const code = String(children);
    if (language === undefined && !code.includes("\n")) {
      return <code className={className}>{children}</code>;
    }
    return <CodeBlock language={language ?? ""} code={code.replace(/\n$/, "")} />;
  },
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
};

export const Markdown = memo(function Markdown({ content }: { content: string }) {
  return (
    <div className="prose prose-stone max-w-none text-[15px] leading-7 dark:prose-invert prose-headings:font-semibold prose-code:rounded prose-code:bg-muted prose-code:px-1 prose-code:py-0.5 prose-code:font-normal prose-code:before:content-none prose-code:after:content-none">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
});
