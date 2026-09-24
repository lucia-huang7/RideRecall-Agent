import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import test from "node:test";
import { compileScript, compileTemplate, parse } from "@vue/compiler-sfc";

const root = fileURLToPath(new URL("../src/", import.meta.url));
function components(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory()
      ? components(join(directory, entry.name))
      : entry.name.endsWith(".vue")
        ? [join(directory, entry.name)]
        : [],
  );
}

test("all template expressions resolve to declared component bindings", () => {
  for (const filename of components(root)) {
    const { descriptor } = parse(readFileSync(filename, "utf8"), { filename });
    const script = compileScript(descriptor, { id: filename });
    const result = compileTemplate({
      source: descriptor.template.content,
      filename,
      id: filename,
      compilerOptions: { bindingMetadata: script.bindings },
    });
    assert.deepEqual(result.errors, [], filename);
    const publicInstance = new Set(["$emit", "$attrs", "$slots", "$refs"]);
    const unresolved = [...result.code.matchAll(/_ctx\.([A-Za-z_$][\w$]*)/g)]
      .map((match) => match[1])
      .filter((name) => !publicInstance.has(name));
    assert.deepEqual(
      [...new Set(unresolved)],
      [],
      `${filename}: undeclared template variables`,
    );
  }
});
