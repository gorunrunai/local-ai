import { Check, Copy, Eye } from "lucide-react";
import { memo, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { post } from "../api/client";
import type { Artifact, Citation } from "../api/types";
import { remarkCitations } from "../lib/citations";
import { remarkBr } from "../lib/remark-br";
import { useChat } from "../store/chat";
import { useUi } from "../store/ui";
import { CitationChip } from "./Citations";
import { repairMermaid } from "../lib/mermaid-repair";

export function useCopy(timeout = 1500): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  return [
    copied,
    (text: string) => {
      navigator.clipboard?.writeText(text).then(
        () => {
          setCopied(true);
          setTimeout(() => setCopied(false), timeout);
        },
        () => undefined,
      );
    },
  ];
}

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (node && typeof node === "object" && "props" in node) return textOf((node as { props: { children?: ReactNode } }).props.children);
  return "";
}

const PREVIEWABLE: Record<string, "html" | "svg" | "react"> = { html: "html", svg: "svg", jsx: "react", tsx: "react" };

async function previewAsArtifact(lang: string, code: string) {
  const cid = useChat.getState().current?.conversation.id;
  if (!cid) return;
  const hash = Array.from(code).reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7).toString(36);
  const art = await post<Artifact>(`/conversations/${cid}/artifacts`, {
    identifier: `snippet-${hash}`, type: PREVIEWABLE[lang], title: `${lang.toUpperCase()} preview`, content: code });
  useUi.getState().openArtifact(art);
}

function CodeBlock({ lang, children }: { lang: string; children: ReactNode }) {
  const [copied, copy] = useCopy();
  const code = textOf(children).replace(/\n$/, "");
  if (lang === "mermaid") return <Mermaid code={code} />;
  const previewable = PREVIEWABLE[lang];
  return (
    <div className="group my-3 overflow-hidden rounded-lg border border-line bg-code">
      <div className="flex items-center border-b border-line px-3 py-1 text-xs text-muted">
        <span className="font-mono">{lang || "text"}</span>
        <span className="flex-1" />
        {previewable && (
          <button type="button" onClick={() => previewAsArtifact(lang, code)} className="mr-1 flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-surface-3 hover:text-fg" aria-label="Preview in artifact panel">
            <Eye size={13} /> Preview
          </button>
        )}
        <button type="button" onClick={() => copy(code)} className="flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-surface-3 hover:text-fg" aria-label="Copy code">
          {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="m-0 overflow-x-auto p-3 text-[13px] leading-relaxed"><code className="font-mono">{children}</code></pre>
    </div>
  );
}

let mermaidLoader: Promise<typeof import("mermaid").default> | null = null;

function Mermaid({ code }: { code: string }) {
  const id = useId().replace(/:/g, "");
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    mermaidLoader ??= import("mermaid").then((m) => m.default);
    const dark = document.documentElement.dataset.theme === "dark" ||
      (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
    mermaidLoader.then(async (mermaid) => {
      const palette = dark
        ? ["#c9c3b6", "#8ab4ff", "#f0b36a", "#f08ac8", "#c3a1f3", "#7fd6d0", "#e8e07a", "#f07a73"]
        : ["#4a4740", "#2b5fb4", "#b86a12", "#b3408a", "#7a4ab5", "#1a8a8a", "#8a7a10", "#bd3b35"];
      mermaid.initialize({
        startOnLoad: false, securityLevel: "strict", theme: "base",
        themeVariables: {
          darkMode: dark, fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif",
          background: dark ? "#151a21" : "#ffffff", primaryColor: dark ? "#1b2129" : "#eef1f4",
          primaryTextColor: dark ? "#e6ebf1" : "#151a21", primaryBorderColor: dark ? "#4c5768" : "#97a2b3",
          lineColor: dark ? "#9aa5b4" : "#5b6574", textColor: dark ? "#e6ebf1" : "#151a21",
          pieStrokeColor: dark ? "#151a21" : "#ffffff", pieTitleTextColor: dark ? "#e6ebf1" : "#151a21",
          pieSectionTextColor: "#ffffff", pieLegendTextColor: dark ? "#e6ebf1" : "#151a21",
          ...Object.fromEntries(palette.map((c, i) => [`pie${i + 1}`, c])),
        },
      });
      try {
        let svg: string;
        try {
          ({ svg } = await mermaid.render(`m${id}`, code));
        } catch (e) {
          // Models often leave labels with quotes or parentheses unquoted: quote them and retry.
          const fixed = repairMermaid(code);
          if (fixed === code) throw e;
          document.getElementById(`dm${id}`)?.remove();
          ({ svg } = await mermaid.render(`m${id}r`, fixed));
        }
        if (!cancelled && ref.current) ref.current.innerHTML = svg;
        setError(null);
      } catch (e) {
        if (!cancelled) setError(String((e as Error).message ?? e));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [code, id]);
  return (
    <div className="my-3 overflow-x-auto rounded-lg border border-line bg-surface p-3">
      {error ? <pre className="text-xs text-danger whitespace-pre-wrap">Diagram error: {error}{"\n\n"}{code}</pre> : <div ref={ref} className="flex justify-center" />}
    </div>
  );
}

interface Props {
  text: string;
  citations?: Citation[];
  streaming?: boolean;
}

export const Markdown = memo(function Markdown({ text, citations = [], streaming }: Props) {
  const known = useMemo(() => new Set(citations.map((c) => c.index)), [citations]);
  const byIndex = useMemo(() => new Map(citations.map((c) => [c.index, c])), [citations]);
  const remarkPlugins = useMemo(() => [remarkGfm, remarkMath, remarkBr, remarkCitations(known)], [known]);
  return (
    <div className={`md ${streaming ? "caret" : ""}`}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={[[rehypeKatex, { throwOnError: false }], [rehypeHighlight, { detect: false, ignoreMissing: true }]]}
        components={{
          pre: ({ children }) => {
            const child = Array.isArray(children) ? children[0] : children;
            const cls = (child as { props?: { className?: string } })?.props?.className ?? "";
            const lang = /language-([\w+-]+)/.exec(cls)?.[1] ?? "";
            return <CodeBlock lang={lang}>{(child as { props?: { children?: ReactNode } })?.props?.children}</CodeBlock>;
          },
          a: ({ href, children }) => {
            const m = /^#cite-(\d+)$/.exec(href ?? "");
            if (m) {
              const c = byIndex.get(Number(m[1]));
              return c ? <CitationChip citation={c} /> : <>{children}</>;
            }
            return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>;
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});
