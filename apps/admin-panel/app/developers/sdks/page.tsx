import type { Metadata } from "next";

import { CodeBlock, PageIntro, SectionCard } from "../portal-ui";

export const metadata: Metadata = {
  title: "SDKs · Portal de desarrollador",
  description:
    "SDKs oficiales Python (agentic-platform-sdk) y TypeScript (@agentic-platform/sdk): instalación y quickstart.",
};

const PY_INSTALL = `pip install -e packages/sdk-python        # desde el monorepo
# o, una vez publicado en el registro interno:
pip install agentic-platform-sdk`;

const PY_QUICKSTART = `from agentic_platform_sdk import ApiClient, V1ProjectCreateRequest

# URL base de la plataforma + un X-API-Token por tenant.
with ApiClient("https://platform.example.com", "tkn_...") as api:
    # listar (paginado)
    for project in api.list_projects(limit=50, offset=0):
        print(project.id, project.name, project.status)

    # obtener uno
    project = api.get_project("11111111-1111-1111-1111-111111111111")

    # crear (requiere un token con scope write)
    created = api.create_project(V1ProjectCreateRequest(name="Mi proyecto"))

    # plans / tasks / conversations son project-scoped
    plans = api.list_plans(created.id)
    tasks = api.list_tasks(created.id)

    # las knowledge bases son tenant-scoped
    kbs = api.list_kbs()`;

const TS_INSTALL = `npm install   # dentro de packages/sdk-typescript (workspace)`;

const TS_QUICKSTART = `import { ApiClient, type V1ProjectCreateRequest } from "@agentic-platform/sdk";

// URL base de la plataforma + un X-API-Token por tenant.
const api = new ApiClient({
  baseUrl: "https://platform.example.com",
  apiToken: "tkn_...",
});

// listar (paginado)
const projects = await api.listProjects({ limit: 50, offset: 0 });

// crear (requiere un token con scope write)
const created = await api.createProject({ name: "Mi proyecto" } satisfies V1ProjectCreateRequest);

// plans / tasks / conversations son project-scoped
const plans = await api.listPlans(created.id);

// las knowledge bases son tenant-scoped
const kbs = await api.listKbs();`;

export default function SdksPage() {
  return (
    <div className="space-y-6">
      <PageIntro
        title="SDKs oficiales"
        lead="Dos clientes tipados sobre la API pública v1. Ambos se generan DESDE el OpenAPI 3.1 en proceso (sin servidor vivo), así que siempre casan con el servidor: modelos generados + un cliente fino escrito a mano que fija X-API-Token una vez y eleva errores tipados."
        testId="sdks-intro"
      />

      <SectionCard title="SDK Python — agentic-platform-sdk" testId="sdks-python">
        <p>
          Dependencias de runtime: <code>httpx</code> + <code>pydantic</code> v2. El token viaja en
          la cabecera <code>X-API-Token</code> en cada request.
        </p>
        <p className="text-foreground font-medium">Instalación</p>
        <CodeBlock lang="bash" code={PY_INSTALL} />
        <p className="text-foreground font-medium">Quickstart</p>
        <CodeBlock lang="python" code={PY_QUICKSTART} />
        <p className="text-xs">
          Una respuesta non-2xx eleva <code>agentic_platform_sdk.ApiError</code> con{" "}
          <code>status_code</code> + <code>body</code>: ramifica por 401 (token malo), 403 (scope),
          404 (cross-tenant) o 429 (rate limit).
        </p>
      </SectionCard>

      <SectionCard title="SDK TypeScript — @agentic-platform/sdk" testId="sdks-typescript">
        <p>
          Cero dependencias de runtime: usa el <code>fetch</code> de la plataforma (Node 18+ /
          navegador); se puede inyectar un <code>fetch</code> propio para tests.
        </p>
        <p className="text-foreground font-medium">Instalación</p>
        <CodeBlock lang="bash" code={TS_INSTALL} />
        <p className="text-foreground font-medium">Quickstart</p>
        <CodeBlock lang="typescript" code={TS_QUICKSTART} />
        <p className="text-xs">
          Una respuesta non-2xx lanza <code>ApiError</code> con <code>statusCode</code> +{" "}
          <code>body</code> (mismas ramas 401 / 403 / 404 / 429).
        </p>
      </SectionCard>

      <SectionCard title="Regeneración (reproducibilidad)" testId="sdks-regenerate">
        <p>
          Tras cualquier cambio del contrato público, regenera los modelos desde el OpenAPI v1
          construido en proceso:
        </p>
        <CodeBlock
          lang="bash"
          code={`python packages/sdk-python/scripts/generate.py
node packages/sdk-typescript/scripts/generate.mjs`}
        />
        <p className="text-xs">
          El código generado se excluye de los linters; el test de cada SDK (paridad modelo ⇄
          schema, cabecera X-API-Token, errores tipados) sí corre en CI.
        </p>
      </SectionCard>
    </div>
  );
}
