/**
 * Regenerate the TypeScript SDK from the v1 OpenAPI spec (Plan 13 task_13_14).
 *
 * Reproducibility entry point: run this whenever the public `/api/v1` contract
 * changes. It does two things, in order, with NO running server needed:
 *
 *  1. Build the v1 OpenAPI 3.1 document IN-PROCESS by invoking Python's
 *     `api_server.routers.api_v1.openapi.build_v1_openapi()` — the SAME
 *     function the live `/api/v1/openapi.json` endpoint serves — and write it
 *     to `packages/sdk-typescript/openapi-v1.json`. So the committed spec the
 *     SDK is generated from is byte-for-byte the published contract.
 *  2. Run `openapi-typescript-codegen` over that spec to (re)write the typed
 *     models under `src/generated/` (fetch client preset). We keep the
 *     generated MODELS and provide our own thin, configurable client in
 *     `src/client.ts` (the generator does not honour the apiKey/X-API-Token
 *     security scheme — see README + client.ts).
 *
 * Usage (from the repo root, dev venv active so Python can import api_server):
 *
 *     node packages/sdk-typescript/scripts/generate.mjs
 *
 * The generated `src/generated/` dir + `openapi-v1.json` are committed; the
 * generated dir is EXCLUDED from the repo's eslint/prettier (see README).
 */
import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const PKG_ROOT = resolve(HERE, "..");
const SPEC_PATH = join(PKG_ROOT, "openapi-v1.json");
const GENERATED_DIR = join(PKG_ROOT, "src", "generated");
// apps/api-server/src must be importable to build the spec in-process.
const API_SERVER_SRC = resolve(PKG_ROOT, "..", "..", "apps", "api-server", "src");

const PY_SNIPPET = [
  "import json, sys",
  `sys.path.insert(0, ${JSON.stringify(API_SERVER_SRC)})`,
  "from api_server.routers.api_v1.openapi import build_v1_openapi",
  "spec = build_v1_openapi()",
  `open(${JSON.stringify(SPEC_PATH)}, "w", encoding="utf-8", newline="\\n").write(`,
  "    json.dumps(spec, indent=2, sort_keys=True) + chr(10))",
  `print("wrote spec ->", ${JSON.stringify(SPEC_PATH)}, "openapi", spec["openapi"], len(spec["paths"]), "paths")`,
].join("\n");

function writeSpec() {
  const python = process.env.PYTHON ?? "python";
  execFileSync(python, ["-c", PY_SNIPPET], { stdio: "inherit" });
}

function generateModels() {
  // Use the package-local openapi-typescript-codegen (devDependency).
  execFileSync(
    process.execPath,
    [
      join(PKG_ROOT, "node_modules", "openapi-typescript-codegen", "bin", "index.js"),
      "--input",
      SPEC_PATH,
      "--output",
      GENERATED_DIR,
      "--client",
      "fetch",
      "--name",
      "AgenticV1Client",
      "--useOptions",
    ],
    { stdio: "inherit" },
  );
  console.log(`wrote models -> ${GENERATED_DIR}`);
}

/**
 * Normalize generated files so the committed tree is hygiene-clean: LF line
 * endings, no trailing whitespace, single final newline. openapi-typescript-
 * codegen writes the host's native endings (CRLF on Windows), which the repo's
 * mixed-line-ending / trailing-whitespace / end-of-file-fixer pre-commit hooks
 * would otherwise re-touch. The generated dir is excluded from the STYLE hooks
 * (prettier/eslint) but the universal hygiene hooks still run on it, so we make
 * the output satisfy them here rather than chase churn on every regeneration.
 */
function normalize(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      normalize(full);
      continue;
    }
    const cleaned =
      readFileSync(full, "utf-8")
        .replace(/\r\n/g, "\n")
        .replace(/[ \t]+\n/g, "\n")
        .replace(/\n+$/, "") + "\n";
    writeFileSync(full, cleaned, "utf-8");
  }
}

writeSpec();
generateModels();
normalize(GENERATED_DIR);
console.log("normalized generated files -> LF, no trailing whitespace, final newline");
console.log("done. Generated SDK is committed; the generated dir is linter-excluded (see README).");
