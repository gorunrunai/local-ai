// Source rewriting for React artifacts: ES module imports/exports → plain code that can be
// evaluated with the libraries passed in as arguments. Pure functions, unit-tested.

export const SUPPORTED_MODULES = ["react", "react-dom", "react-dom/client", "lucide-react", "recharts"] as const;

export class ArtifactError extends Error {}

const IMPORT_RE = /^\s*import\s+(?:(type)\s+)?([\s\S]*?)\s+from\s+["']([^"']+)["'];?[^\S\n]*$/gm;
const SIDE_EFFECT_IMPORT_RE = /^\s*import\s+["']([^"']+)["'];?\s*$/gm;

/** `a, { b as c, d }` → destructuring pieces. */
function bindings(clause: string, mod: string): string[] {
  const out: string[] = [];
  const ref = `__mods[${JSON.stringify(mod)}]`;
  let rest = clause.trim();
  const ns = /^\*\s+as\s+(\w+)$/.exec(rest);
  if (ns) return [`const ${ns[1]} = ${ref};`];
  const named = /\{([\s\S]*)\}/.exec(rest);
  if (named) {
    const parts = named[1].split(",").map((p) => p.trim()).filter(Boolean)
      .filter((p) => !p.startsWith("type "))
      .map((p) => {
        const m = /^(\w+)(?:\s+as\s+(\w+))?$/.exec(p);
        if (!m) throw new ArtifactError(`Unsupported import specifier "${p}"`);
        return m[2] ? `${m[1]}: ${m[2]}` : m[1];
      });
    if (parts.length) out.push(`const { ${parts.join(", ")} } = ${ref};`);
    rest = rest.replace(named[0], "").replace(/,\s*$/, "").trim();
  }
  const def = rest.replace(/,$/, "").trim();
  if (def) {
    if (def.startsWith("*")) {
      const m = /^\*\s+as\s+(\w+)$/.exec(def);
      if (m) out.push(`const ${m[1]} = ${ref};`);
    } else {
      out.push(`const ${def} = (${ref} && ${ref}.default) || ${ref};`);
    }
  }
  return out;
}

export interface Rewritten {
  code: string;
  defaultName: string;
}

/** Rewrite imports to `__mods[...]` lookups and capture the default export's name. */
export function rewriteModule(source: string): Rewritten {
  let code = source.replace(IMPORT_RE, (_m, isType: string | undefined, clause: string, mod: string) => {
    if (isType) return "";
    if (!(SUPPORTED_MODULES as readonly string[]).includes(mod)) {
      throw new ArtifactError(`"${mod}" isn't available in artifacts. Available: ${SUPPORTED_MODULES.join(", ")}.`);
    }
    return bindings(clause, mod).join("\n");
  });
  code = code.replace(SIDE_EFFECT_IMPORT_RE, "");

  let defaultName = "";
  code = code.replace(/export\s+default\s+(async\s+)?function\s*(\w*)\s*\(/, (_m, asyncKw: string | undefined, name: string) => {
    defaultName = name || "__DefaultExport";
    return `${asyncKw ?? ""}function ${defaultName}(`;
  });
  if (!defaultName) {
    code = code.replace(/export\s+default\s+class\s+(\w+)/, (_m, name: string) => {
      defaultName = name;
      return `class ${name}`;
    });
  }
  if (!defaultName) {
    code = code.replace(/export\s+default\s+(\w+)\s*;?\s*$/m, (_m, name: string) => {
      defaultName = name;
      return "";
    });
  }
  if (!defaultName && /export\s+default\s+/.test(code)) {
    code = code.replace(/export\s+default\s+/, "const __DefaultExport = ");
    defaultName = "__DefaultExport";
  }
  code = code.replace(/^(\s*)export\s+(?=(const|let|var|function|class|async)\b)/gm, "$1");
  if (!defaultName) {
    // No default export: fall back to the last capitalized component declared.
    const decls = [...code.matchAll(/(?:function|const|class)\s+([A-Z]\w*)/g)];
    if (!decls.length) throw new ArtifactError("The component needs a default export (export default function App…).");
    defaultName = decls[decls.length - 1][1];
  }
  return { code, defaultName };
}
