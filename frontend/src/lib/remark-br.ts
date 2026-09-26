import type { Break, Html, Root } from "mdast";
import { visit } from "unist-util-visit";

/**
 * Models often write `<br>` for a line break inside a Markdown table cell. Raw HTML isn't rendered
 * (for safety), so without this the tag shows up as text. Turn exactly `<br>`, `<br/>` and `<br />`
 * into real line breaks; any other HTML is still shown as text.
 */
export function remarkBr() {
  return (tree: Root) => {
    visit(tree, "html", (node: Html, index, parent) => {
      if (parent && index !== undefined && /^<br\s*\/?>$/i.test(node.value.trim())) {
        parent.children.splice(index, 1, { type: "break" } as Break);
      }
    });
  };
}
