/**
 * Fix the most common mistake models make in Mermaid flowcharts: node labels with quotes,
 * parentheses or other punctuation that aren't quoted, e.g. `A[Tokens: "the", "cat"]` or
 * `E[QK^T / sqrt(d)]`. Mermaid needs those labels in double quotes, with inner quotes as #quot;.
 * Used only after a diagram fails to render, so valid diagrams are never touched.
 */

// Node shapes, longest first so `([` wins over `(`.
const SHAPES: [string, string][] = [
  ["([", "])"], ["[[", "]]"], ["[(", ")]"], ["((", "))"], ["{{", "}}"], ["[/", "/]"], ["[\\", "\\]"],
  ["[/", "\\]"], ["[\\", "/]"], ["[", "]"], ["(", ")"], ["{", "}"], [">", "]"],
];
// What may follow a node: the end of the statement, a link, `&`, a class (:::) or `;`.
const AFTER = /^\s*(?:$|;|&|:::|-->|---|-\.|==|~~~|<-|--|-[x.o]|\|)/;
const RISKY = /["()[\]{}<>|;#&]/;

function quote(label: string): string {
  const inner = label.trim();
  if (/^"[^"]*"$/.test(inner)) return label;           // already quoted properly
  // A label quoted at both ends but with quotes inside too: keep the outer pair, escape the rest.
  const body = inner.length > 1 && inner.startsWith('"') && inner.endsWith('"') ? inner.slice(1, -1) : inner;
  return `"${body.replace(/"/g, "#quot;")}"`;
}

function repairLine(line: string): string {
  let out = "", i = 0;
  while (i < line.length) {
    const id = /^[A-Za-z_][\w-]*/.exec(line.slice(i));
    // A node id at the start of a token (not inside a word or a quoted string).
    if (id && (i === 0 || /[\s&;>|-]/.test(line[i - 1]))) {
      const at = i + id[0].length;
      const shape = SHAPES.find(([open]) => line.startsWith(open, at));
      if (shape) {
        const [open, close] = shape;
        const start = at + open.length;
        // The closing delimiter is the first one followed by something that can follow a node.
        let end = line.indexOf(close, start);
        while (end !== -1 && !AFTER.test(line.slice(end + close.length))) end = line.indexOf(close, end + 1);
        if (end !== -1) {
          const label = line.slice(start, end);
          out += id[0] + open + (RISKY.test(label) ? quote(label) : label) + close;
          i = end + close.length;
          continue;
        }
      }
      out += id[0];
      i = at;
      continue;
    }
    // Link text: -->|text| — quote it when it has parentheses or quotes too.
    if (line[i] === "|" && /(?:-->|---|-\.->|==>|--[xo])\s*$/.test(line.slice(0, i))) {
      const end = line.indexOf("|", i + 1);
      if (end !== -1) {
        const text = line.slice(i + 1, end);
        out += "|" + (/["()[\]{}]/.test(text) ? quote(text) : text) + "|";
        i = end + 1;
        continue;
      }
    }
    out += line[i++];
  }
  return out;
}

export function repairMermaid(code: string): string {
  const first = code.split("\n").find((l) => l.trim() && !l.trim().startsWith("%%"))?.trim() ?? "";
  if (!/^(graph|flowchart)\b/.test(first)) return code;
  return code.split("\n").map((l) => (/^\s*(%%|subgraph\b|end\b|classDef|class\b|style\b|linkStyle|click\b)/.test(l) ? l : repairLine(l))).join("\n");
}
