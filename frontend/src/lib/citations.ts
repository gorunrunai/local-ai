import type { Link, Parent, Root, Text } from "mdast";
import { visit } from "unist-util-visit";

const CITE = /\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]/g;

/**
 * remark plugin: turn "[3]" / "[1, 2]" in prose into links with href "#cite-3" that the
 * Markdown renderer shows as citation chips. Code spans/blocks are untouched (they aren't
 * `text` nodes), and existing links are skipped.
 */
export function remarkCitations(known: Set<number>) {
  return () => (tree: Root) => {
    visit(tree, "text", (node: Text, index, parent: Parent | undefined) => {
      if (!parent || index === undefined || parent.type === "link") return;
      const value = node.value;
      CITE.lastIndex = 0;
      if (!CITE.test(value)) return;
      CITE.lastIndex = 0;
      const out: (Text | Link)[] = [];
      let last = 0;
      for (const m of value.matchAll(CITE)) {
        const nums = m[1].split(",").map((n) => parseInt(n.trim(), 10));
        if (!nums.every((n) => known.has(n))) continue;
        if (m.index! > last) out.push({ type: "text", value: value.slice(last, m.index) });
        for (const n of nums) {
          out.push({ type: "link", url: `#cite-${n}`, children: [{ type: "text", value: String(n) }] });
        }
        last = m.index! + m[0].length;
      }
      if (!out.length) return;
      if (last < value.length) out.push({ type: "text", value: value.slice(last) });
      parent.children.splice(index, 1, ...out);
      return index + out.length;
    });
  };
}
